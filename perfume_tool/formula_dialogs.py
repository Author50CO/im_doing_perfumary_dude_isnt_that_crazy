import os
import tkinter as tk
from tkinter import ttk, messagebox


class FormulaSelectDialog:
    """
    Checkbox selection dialog for formula open/import/export.
    """

    def __init__(
        self,
        parent,
        title: str,
        formulas: list[dict],
        on_confirm,
        confirm_text: str = "OK",
        allow_multiple: bool = True,
        show_select_all: bool = True,
    ):
        self.parent = parent
        self.title = title
        self.formulas = formulas
        self.on_confirm = on_confirm
        self.confirm_text = confirm_text
        self.allow_multiple = allow_multiple
        self.show_select_all = show_select_all and allow_multiple

        self.vars = []
        self.select_all_var = tk.BooleanVar(value=False)

        self.window = tk.Toplevel(parent)
        self.window.title(title)
        self.window.geometry("760x520")
        self.window.transient(parent)
        self.window.grab_set()

        self.build_ui()

    def build_ui(self):
        top = ttk.Frame(self.window, padding=10)
        top.pack(fill="x")

        ttk.Label(
            top,
            text=self.title,
            font=("Segoe UI", 11, "bold"),
        ).pack(side="left")

        if self.show_select_all:
            ttk.Checkbutton(
                top,
                text="Select All",
                variable=self.select_all_var,
                command=self.toggle_select_all,
            ).pack(side="right")

        container = ttk.Frame(self.window, padding=(10, 0, 10, 10))
        container.pack(fill="both", expand=True)

        self.canvas = tk.Canvas(container, highlightthickness=0)
        self.scrollbar = ttk.Scrollbar(
            container,
            orient="vertical",
            command=self.canvas.yview,
        )
        self.list_frame = ttk.Frame(self.canvas)

        self.list_frame.bind(
            "<Configure>",
            lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")),
        )

        self.canvas_window = self.canvas.create_window(
            (0, 0),
            window=self.list_frame,
            anchor="nw",
        )

        self.canvas.configure(yscrollcommand=self.scrollbar.set)

        self.canvas.pack(side="left", fill="both", expand=True)
        self.scrollbar.pack(side="right", fill="y")

        self.canvas.bind("<Configure>", self.on_canvas_configure)

        self.populate_list()

        bottom = ttk.Frame(self.window, padding=10)
        bottom.pack(fill="x")

        ttk.Button(
            bottom,
            text=self.confirm_text,
            command=self.confirm,
        ).pack(side="right", padx=(5, 0))

        ttk.Button(
            bottom,
            text="Cancel",
            command=self.window.destroy,
        ).pack(side="right")

    def on_canvas_configure(self, event):
        self.canvas.itemconfigure(self.canvas_window, width=event.width)

    def populate_list(self):
        for i, formula in enumerate(self.formulas):
            var = tk.BooleanVar(value=False)
            self.vars.append(var)

            row = ttk.Frame(self.list_frame, padding=(6, 6))
            row.pack(fill="x", expand=True)

            cb = ttk.Checkbutton(
                row,
                variable=var,
                command=lambda idx=i: self.on_item_toggle(idx),
            )
            cb.pack(side="left", padx=(0, 8))

            info = ttk.Frame(row)
            info.pack(side="left", fill="x", expand=True)

            name = str(formula.get("name") or "Untitled Formula")
            date = str(formula.get("created_date") or "")
            description = str(formula.get("description") or "")
            rows = formula.get("rows") or []
            filename = formula.get("filename") or os.path.basename(formula.get("file_path", ""))

            title = name
            if date:
                title += f"  |  {date}"

            ttk.Label(
                info,
                text=title,
                font=("Segoe UI", 10, "bold"),
            ).pack(anchor="w")

            detail = f"{len(rows)} materials"
            if filename:
                detail += f"  |  {filename}"

            ttk.Label(
                info,
                text=detail,
                foreground="#4b5563",
            ).pack(anchor="w")

            if description:
                ttk.Label(
                    info,
                    text=description,
                    foreground="#374151",
                    wraplength=620,
                ).pack(anchor="w", pady=(2, 0))

            ttk.Separator(self.list_frame).pack(fill="x", padx=6, pady=2)

    def on_item_toggle(self, idx: int):
        if not self.allow_multiple and self.vars[idx].get():
            for i, var in enumerate(self.vars):
                if i != idx:
                    var.set(False)

    def toggle_select_all(self):
        state = self.select_all_var.get()

        for var in self.vars:
            var.set(state)

    def selected_formulas(self) -> list[dict]:
        selected = []

        for formula, var in zip(self.formulas, self.vars):
            if var.get():
                selected.append(formula)

        return selected

    def confirm(self):
        selected = self.selected_formulas()

        if not selected:
            messagebox.showwarning("No selection", "Select at least one formula.")
            return

        if not self.allow_multiple and len(selected) > 1:
            messagebox.showwarning("Too many selected", "Select only one formula.")
            return

        self.on_confirm(selected)
        self.window.destroy()