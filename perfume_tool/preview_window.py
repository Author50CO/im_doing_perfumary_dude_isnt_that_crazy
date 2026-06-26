import tkinter as tk
from tkinter import ttk, messagebox


class ParsePreviewWindow:
    def __init__(self, parent, parsed_rows, on_apply):
        self.parent = parent
        self.parsed_rows = parsed_rows
        self.on_apply = on_apply

        self.window = tk.Toplevel(parent)
        self.window.title("Parse Preview")
        self.window.geometry("1100x650")
        self.window.transient(parent)
        self.window.grab_set()

        self.build_ui()
        self.load_rows()

    def build_ui(self):
        top = ttk.Frame(self.window, padding=8)
        top.pack(fill="x")

        ttk.Label(
            top,
            text=(
                "Green rows will be imported. Red rows will be skipped. "
                "Double-click to edit. Double-click Include to toggle."
            ),
            font=("Segoe UI", 10, "bold"),
        ).pack(side="left")

        ttk.Button(
            top,
            text="Apply Accepted Rows",
            command=self.apply,
        ).pack(side="right", padx=(5, 0))

        ttk.Button(
            top,
            text="Cancel",
            command=self.window.destroy,
        ).pack(side="right")

        frame = ttk.Frame(self.window, padding=8)
        frame.pack(fill="both", expand=True)

        columns = ("include", "material", "part", "dilution", "reason", "original")
        self.tree = ttk.Treeview(frame, columns=columns, show="headings")

        headings = {
            "include": "Include",
            "material": "Material",
            "part": "Part",
            "dilution": "Dilution (%)",
            "reason": "Reason / Parser",
            "original": "Original Line",
        }

        widths = {
            "include": 70,
            "material": 270,
            "part": 80,
            "dilution": 100,
            "reason": 170,
            "original": 390,
        }

        for col in columns:
            self.tree.heading(col, text=headings[col])
            self.tree.column(col, width=widths[col], anchor="w")

        self.tree.tag_configure("accepted", foreground="green")
        self.tree.tag_configure("rejected", foreground="red")

        yscroll = ttk.Scrollbar(frame, orient="vertical", command=self.tree.yview)
        xscroll = ttk.Scrollbar(frame, orient="horizontal", command=self.tree.xview)

        self.tree.configure(
            yscrollcommand=yscroll.set,
            xscrollcommand=xscroll.set,
        )

        self.tree.grid(row=0, column=0, sticky="nsew")
        yscroll.grid(row=0, column=1, sticky="ns")
        xscroll.grid(row=1, column=0, sticky="ew")

        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)

        self.tree.bind("<Double-1>", self.on_double_click)

    def load_rows(self):
        for row in self.parsed_rows:
            include = bool(row.get("include", False))
            tag = "accepted" if include else "rejected"

            self.tree.insert(
                "",
                "end",
                values=(
                    "Yes" if include else "No",
                    row.get("material", ""),
                    row.get("part", ""),
                    row.get("dilution", ""),
                    row.get("reason", ""),
                    row.get("original", ""),
                ),
                tags=(tag,),
            )

    def on_double_click(self, event):
        if self.tree.identify("region", event.x, event.y) != "cell":
            return

        item_id = self.tree.identify_row(event.y)
        column_id = self.tree.identify_column(event.x)

        if not item_id or not column_id:
            return

        col_index = int(column_id.replace("#", "")) - 1
        columns = ("include", "material", "part", "dilution", "reason", "original")
        col_name = columns[col_index]

        if col_name == "include":
            values = list(self.tree.item(item_id, "values"))
            values[0] = "No" if values[0] == "Yes" else "Yes"
            self.tree.item(item_id, values=values)
            self.update_row_tag(item_id)
            return

        if col_name in {"reason", "original"}:
            return

        bbox = self.tree.bbox(item_id, column_id)
        if not bbox:
            return

        x, y, width, height = bbox

        entry = ttk.Entry(self.tree)
        entry.place(x=x, y=y, width=width, height=height)
        entry.insert(0, self.tree.set(item_id, col_name))
        entry.focus()

        def save_edit(event=None):
            self.tree.set(item_id, col_name, entry.get().strip())
            entry.destroy()

            values = list(self.tree.item(item_id, "values"))
            material = str(values[1]).strip()
            part = str(values[2]).strip()

            if material and self.is_positive_number(part):
                values[0] = "Yes"
                self.tree.item(item_id, values=values)

            self.update_row_tag(item_id)

        entry.bind("<Return>", save_edit)
        entry.bind("<FocusOut>", save_edit)
        entry.bind("<Escape>", lambda e: entry.destroy())

    def update_row_tag(self, item_id):
        values = self.tree.item(item_id, "values")
        self.tree.item(
            item_id,
            tags=("accepted",) if values[0] == "Yes" else ("rejected",),
        )

    @staticmethod
    def is_positive_number(value):
        try:
            return float(value) > 0
        except Exception:
            return False

    def apply(self):
        """
        Important:
        Preview keeps the parsed/original part as-is.

        Example:
            Calone 10% 10

        Accepted row sent to app.py:
            part = 10
            parsed_part = 10
            dilution = 10
            manual_dilution = 10

        app.py will convert this to effective part:
            effective part = 10 * 10 / 100 = 1
        """
        accepted = []
        errors = []

        for idx, item_id in enumerate(self.tree.get_children(), start=1):
            values = self.tree.item(item_id, "values")

            include = values[0] == "Yes"
            material = str(values[1]).strip()
            part = str(values[2]).strip()
            dilution = str(values[3]).strip()

            if not include:
                continue

            if not material:
                errors.append(f"Row {idx}: missing material name")
                continue

            try:
                part_float = float(part)
                if part_float <= 0:
                    raise ValueError
            except ValueError:
                errors.append(f"Row {idx}: invalid part number: {part}")
                continue

            if dilution:
                try:
                    dilution_float = float(dilution)
                    if dilution_float <= 0:
                        raise ValueError
                except ValueError:
                    errors.append(f"Row {idx}: invalid dilution: {dilution}")
                    continue

            accepted.append(
                {
                    "material": material,
                    "part": part_float,
                    "parsed_part": part_float,
                    "raw_part": part_float,
                    "dilution": dilution,
                    "parsed_dilution": dilution,
                    "manual_dilution": dilution,
                }
            )

        if errors:
            messagebox.showerror(
                "Cannot apply",
                "Please fix these rows first:\n\n" + "\n".join(errors[:15]),
            )
            return

        if not accepted:
            messagebox.showwarning(
                "No accepted rows",
                "No rows are marked as Include = Yes.",
            )
            return

        self.on_apply(accepted)
        self.window.destroy()