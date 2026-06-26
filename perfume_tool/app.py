import csv
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from datetime import datetime

from PIL import Image, ImageGrab
from tksheet import Sheet

from .calculator import CalcInputs, PerfumeCalculator
from .ocr import run_ocr_on_image
from .ocr_column_selector import OCRColumnSelectorWindow
from .parser import parse_formula_text_advanced
from .preview_window import ParsePreviewWindow
from .utils import safe_float

from .formula_storage import (
    formulas_dir,
    save_formula_file,
    load_formula_file,
    list_local_formulas,
    read_formulas_from_xml,
    export_formulas_to_bundle,
    import_formulas_to_local,
)
from .formula_dialogs import FormulaSelectDialog


def effective_part_from_parsed_part(part, dilution) -> tuple[float, bool]:
    part_value = safe_float(part, None)
    dilution_value = safe_float(dilution, None)

    if part_value is None:
        return 0.0, False

    if dilution_value is None:
        return float(part_value), False

    if dilution_value <= 0:
        return float(part_value), False

    effective = float(part_value) * float(dilution_value) / 100.0
    changed = abs(effective - float(part_value)) > 1e-12

    return effective, changed


class PerfumeCalculatorApp:
    HEADERS = [
        "Material",
        "Part",
        "Raw Material (%)",
        "Manual Dilution (%)",
        "Applied Dilution (%)",
        "Weight (g)",
    ]

    (
        COL_MATERIAL,
        COL_PART,
        COL_RAW_PCT,
        COL_MANUAL_DILUTION,
        COL_APPLIED_DILUTION,
        COL_WEIGHT,
    ) = range(6)

    def __init__(self, root):
        self.root = root
        self.root.title("Perfume Dilution Calculator")
        self.root.geometry("1280x800")

        self.rows = []
        self.current_output_rows = []

        self._refreshing_table = False
        self._sort_column = None
        self._sort_reverse = False

        self.formula_name_var = tk.StringVar(value="Untitled Formula")
        self.formula_date_var = tk.StringVar(value=datetime.now().strftime("%Y-%m-%d"))

        self.target_weight_var = tk.StringVar(value="10")
        self.default_dilution_var = tk.StringVar(value="20")
        self.desired_dilution_var = tk.StringVar(value="10")
        self.maximum_dilution_var = tk.StringVar(value="30")

        self.current_dilution_var = tk.StringVar(value="-")
        self.force_concentrated_var = tk.StringVar(value="-")
        self.additional_solvent_var = tk.StringVar(value="-")
        self.net_weight_var = tk.StringVar(value="-")
        self.total_parts_var = tk.StringVar(value="-")

        self.build_ui()

    def build_ui(self):
        main = ttk.Frame(self.root, padding=10)
        main.pack(fill="both", expand=True)

        left = ttk.Frame(main)
        left.pack(side="left", fill="both", expand=False, padx=(0, 10))

        right = ttk.Frame(main)
        right.pack(side="right", fill="both", expand=True)

        ttk.Label(
            left,
            text="Paste formula text",
            font=("Segoe UI", 11, "bold"),
        ).pack(anchor="w")

        self.input_text = tk.Text(left, width=47, height=16, wrap="word")
        self.input_text.pack(fill="x", pady=(5, 8))

        btn_frame = ttk.Frame(left)
        btn_frame.pack(fill="x", pady=(0, 10))

        ttk.Button(
            btn_frame,
            text="Parse Formula",
            command=self.parse_formula,
        ).pack(side="left", padx=(0, 5))

        ttk.Button(
            btn_frame,
            text="Import Image OCR",
            command=self.import_image_ocr,
        ).pack(side="left", padx=(0, 5))

        ttk.Button(
            btn_frame,
            text="Paste Image OCR",
            command=self.paste_image_ocr,
        ).pack(side="left", padx=(0, 5))

        ttk.Button(
            btn_frame,
            text="Clear",
            command=self.clear_all,
        ).pack(side="left")

        inputs = ttk.LabelFrame(left, text="Inputs", padding=10)
        inputs.pack(fill="x", pady=(5, 10))

        self.add_input(inputs, "Target Weight (g)", self.target_weight_var, 0)
        self.add_input(inputs, "Default Dilution (%)", self.default_dilution_var, 1)
        self.add_input(inputs, "Desired Perfume Dilution (%)", self.desired_dilution_var, 2)
        self.add_input(inputs, "Maximum Dilution (%)", self.maximum_dilution_var, 3)

        ttk.Button(
            inputs,
            text="Calculate",
            command=self.calculate,
        ).grid(row=4, column=0, columnspan=2, sticky="ew", pady=(10, 0))

        outputs = ttk.LabelFrame(left, text="Outputs", padding=10)
        outputs.pack(fill="x")

        self.add_output(outputs, "Total Parts", self.total_parts_var, 0)
        self.add_output(outputs, "Current Possible Dilution (%)", self.current_dilution_var, 1)
        self.add_output(outputs, "Force Concentrated Materials", self.force_concentrated_var, 2)
        self.add_output(outputs, "Additional Solvent (g)", self.additional_solvent_var, 3)
        self.add_output(outputs, "Net Weight (g)", self.net_weight_var, 4)

        formula_frame = ttk.LabelFrame(right, text="Formula", padding=10)
        formula_frame.pack(fill="x", pady=(0, 8))

        ttk.Label(formula_frame, text="Formula Name").grid(row=0, column=0, sticky="w")
        ttk.Entry(
            formula_frame,
            textvariable=self.formula_name_var,
            width=42,
        ).grid(row=0, column=1, sticky="ew", padx=(8, 12))

        ttk.Label(formula_frame, text="Created Date").grid(row=0, column=2, sticky="w")
        ttk.Entry(
            formula_frame,
            textvariable=self.formula_date_var,
            width=14,
        ).grid(row=0, column=3, sticky="e", padx=(8, 0))

        ttk.Label(formula_frame, text="Description").grid(row=1, column=0, sticky="nw", pady=(8, 0))

        self.description_text = tk.Text(
            formula_frame,
            height=3,
            width=70,
            wrap="word",
        )
        self.description_text.grid(row=1, column=1, columnspan=3, sticky="ew", padx=(8, 0), pady=(8, 0))

        formula_frame.columnconfigure(1, weight=1)

        formula_btns = ttk.Frame(formula_frame)
        formula_btns.grid(row=2, column=0, columnspan=4, sticky="ew", pady=(10, 0))

        ttk.Button(
            formula_btns,
            text="Open Formula",
            command=self.open_formula,
        ).pack(side="left", padx=(0, 5))

        ttk.Button(
            formula_btns,
            text="Save Formula",
            command=self.save_formula,
        ).pack(side="left", padx=(0, 5))

        ttk.Button(
            formula_btns,
            text="Import Formula",
            command=self.import_formula_xml,
        ).pack(side="left", padx=(16, 5))

        ttk.Button(
            formula_btns,
            text="Export Formula",
            command=self.export_formula_xml,
        ).pack(side="left", padx=(0, 5))

        ttk.Label(
            formula_btns,
            text=f"XML folder: {formulas_dir()}",
            foreground="#6b7280",
        ).pack(side="right")

        ttk.Label(
            right,
            text="Materials / Dilution / Weight",
            font=("Segoe UI", 11, "bold"),
        ).pack(anchor="w")

        table_frame = ttk.Frame(right)
        table_frame.pack(fill="both", expand=True, pady=(5, 0))

        self.sheet = Sheet(
            table_frame,
            headers=self.HEADERS,
            width=860,
            height=620,
            show_x_scrollbar=True,
            show_y_scrollbar=True,
        )

        self.sheet.enable_bindings(
            "single_select",
            "row_select",
            "column_select",
            "drag_select",
            "arrowkeys",
            "right_click_popup_menu",
            "rc_select",
            "copy",
            "paste",
            "delete",
            "undo",
            "edit_cell",
            "column_width_resize",
            "row_height_resize",
            "double_click_column_resize",
        )

        self.sheet.pack(fill="both", expand=True)

        self.sheet.extra_bindings(
            [
                ("end_edit_cell", self.on_sheet_edit),
                ("edit_cell", self.on_sheet_edit),
                ("column_select", self.on_column_select_for_sort),
            ]
        )

        self.style_sheet()

    def add_input(self, parent, label, var, row):
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=3)
        ttk.Entry(parent, textvariable=var, width=14).grid(
            row=row,
            column=1,
            sticky="e",
            pady=3,
        )

    def add_output(self, parent, label, var, row):
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=3)
        ttk.Label(
            parent,
            textvariable=var,
            font=("Segoe UI", 10, "bold"),
        ).grid(row=row, column=1, sticky="e", pady=3)

    def style_sheet(self):
        self.sheet.set_options(
            font=("Segoe UI", 10, "normal"),
            header_font=("Segoe UI", 10, "bold"),
            index_font=("Segoe UI", 9, "normal"),
            table_bg="#ffffff",
            table_fg="#111827",
            header_bg="#111827",
            header_fg="#f9fafb",
            index_bg="#f3f4f6",
            index_fg="#6b7280",
            top_left_bg="#111827",
            selected_bg="#dbeafe",
            selected_fg="#111827",
            selected_rows_bg="#e0f2fe",
            selected_rows_fg="#111827",
            selected_columns_bg="#e0f2fe",
            selected_columns_fg="#111827",
            grid_color="#e5e7eb",
            align="w",
            header_align="center",
            row_height=28,
        )

        self.sheet.set_column_widths([300, 85, 125, 145, 150, 120])

        for col in [
            self.COL_PART,
            self.COL_RAW_PCT,
            self.COL_MANUAL_DILUTION,
            self.COL_APPLIED_DILUTION,
            self.COL_WEIGHT,
        ]:
            self.sheet.align_columns(columns=[col], align="e")

        self.sheet.align_columns(columns=[self.COL_MATERIAL], align="w")

    def get_inputs(self) -> CalcInputs:
        vals = [
            safe_float(v.get())
            for v in [
                self.target_weight_var,
                self.default_dilution_var,
                self.desired_dilution_var,
                self.maximum_dilution_var,
            ]
        ]

        labels = [
            "Target Weight",
            "Default Dilution",
            "Desired Dilution",
            "Maximum Dilution",
        ]

        for label, val in zip(labels, vals):
            if val is None or val <= 0:
                raise ValueError(f"{label} must be greater than 0.")

        return CalcInputs(*vals)

    def description_value(self) -> str:
        return self.description_text.get("1.0", "end").strip()

    def set_description_value(self, value: str):
        self.description_text.delete("1.0", "end")
        self.description_text.insert("1.0", str(value or ""))

    def current_formula_dict(self) -> dict:
        return {
            "name": self.formula_name_var.get().strip() or "Untitled Formula",
            "created_date": self.formula_date_var.get().strip() or datetime.now().strftime("%Y-%m-%d"),
            "description": self.description_value(),
            "inputs": {
                "target_weight": self.target_weight_var.get(),
                "default_dilution": self.default_dilution_var.get(),
                "desired_dilution": self.desired_dilution_var.get(),
                "maximum_dilution": self.maximum_dilution_var.get(),
            },
            "source_text": self.input_text.get("1.0", "end").strip(),
            "rows": self.rows,
        }

    def load_formula_dict(self, formula: dict):
        self.formula_name_var.set(formula.get("name", "Untitled Formula"))
        self.formula_date_var.set(
            formula.get("created_date") or datetime.now().strftime("%Y-%m-%d")
        )
        self.set_description_value(formula.get("description", ""))

        inputs = formula.get("inputs") or {}

        self.target_weight_var.set(str(inputs.get("target_weight", "10")))
        self.default_dilution_var.set(str(inputs.get("default_dilution", "20")))
        self.desired_dilution_var.set(str(inputs.get("desired_dilution", "10")))
        self.maximum_dilution_var.set(str(inputs.get("maximum_dilution", "30")))

        self.input_text.delete("1.0", "end")
        self.input_text.insert("1.0", formula.get("source_text", ""))

        self.rows = formula.get("rows", [])

        self._sort_column = None
        self._sort_reverse = False

        self.calculate()

    def save_formula(self):
        if not self.rows:
            messagebox.showwarning("No formula", "There are no formula rows to save.")
            return

        try:
            path = save_formula_file(self.current_formula_dict())
            messagebox.showinfo("Formula saved", f"Saved formula XML:\n{path}")
        except Exception as e:
            messagebox.showerror("Save Formula Error", str(e))

    def open_formula(self):
        formulas = list_local_formulas()

        if not formulas:
            messagebox.showinfo(
                "No formulas",
                f"No saved formulas found.\n\nFolder:\n{formulas_dir()}",
            )
            return

        def on_selected(selected):
            self.load_formula_dict(selected[0])

        FormulaSelectDialog(
            self.root,
            "Open Formula",
            formulas,
            on_confirm=on_selected,
            confirm_text="Open Selected",
            allow_multiple=False,
            show_select_all=False,
        )

    def export_formula_xml(self):
        formulas = list_local_formulas()

        if not formulas:
            messagebox.showinfo(
                "No formulas",
                f"No saved formulas found.\n\nFolder:\n{formulas_dir()}",
            )
            return

        def on_selected(selected):
            file_path = filedialog.asksaveasfilename(
                title="Export Formula XML",
                defaultextension=".xml",
                filetypes=[
                    ("XML files", "*.xml"),
                    ("All files", "*.*"),
                ],
            )

            if not file_path:
                return

            try:
                export_formulas_to_bundle(selected, file_path)
                messagebox.showinfo(
                    "Export complete",
                    f"Exported {len(selected)} formula(s):\n{file_path}",
                )
            except Exception as e:
                messagebox.showerror("Export Formula Error", str(e))

        FormulaSelectDialog(
            self.root,
            "Export Formula",
            formulas,
            on_confirm=on_selected,
            confirm_text="Export Selected",
            allow_multiple=True,
            show_select_all=True,
        )

    def import_formula_xml(self):
        file_path = filedialog.askopenfilename(
            title="Import Formula XML",
            filetypes=[
                ("XML files", "*.xml"),
                ("All files", "*.*"),
            ],
        )

        if not file_path:
            return

        try:
            formulas = read_formulas_from_xml(file_path)
        except Exception as e:
            messagebox.showerror("Import Formula Error", str(e))
            return

        if not formulas:
            messagebox.showwarning("No formulas", "No formulas found in this XML.")
            return

        def on_selected(selected):
            try:
                saved_paths = import_formulas_to_local(selected)
                messagebox.showinfo(
                    "Import complete",
                    f"Imported {len(saved_paths)} formula(s) to:\n{formulas_dir()}",
                )
            except Exception as e:
                messagebox.showerror("Import Formula Error", str(e))

        FormulaSelectDialog(
            self.root,
            "Import Formula",
            formulas,
            on_confirm=on_selected,
            confirm_text="Import Selected",
            allow_multiple=True,
            show_select_all=True,
        )

    def parse_formula(self):
        text = self.input_text.get("1.0", "end").strip()

        if not text:
            messagebox.showwarning("Empty input", "Paste formula text first.")
            return

        parsed = parse_formula_text_advanced(text)

        if not parsed:
            messagebox.showwarning("No rows", "No parseable lines found.")
            return

        ParsePreviewWindow(self.root, parsed, self.apply_parsed_rows)

    def apply_parsed_rows(self, accepted_rows):
        rows = []

        for r in accepted_rows:
            material = str(r.get("material", "")).strip()

            parsed_part = r.get("parsed_part", r.get("raw_part", r.get("part", "")))
            parsed_dilution = str(
                r.get(
                    "parsed_dilution",
                    r.get("dilution", r.get("manual_dilution", "")),
                )
            ).strip()

            effective_part, changed = effective_part_from_parsed_part(
                parsed_part,
                parsed_dilution,
            )

            if not material:
                continue

            if effective_part <= 0:
                continue

            rows.append(
                {
                    "material": material,
                    "part": effective_part,
                    "manual_dilution": parsed_dilution,
                    "part_adjusted_by_dilution": changed,
                    "parsed_part": parsed_part,
                    "parsed_dilution": parsed_dilution,
                }
            )

        self.rows = rows
        self.calculate()

    def import_image_ocr(self):
        file_path = filedialog.askopenfilename(
            title="Select formula image",
            filetypes=[
                ("Image files", "*.png *.jpg *.jpeg *.bmp *.tif *.tiff"),
                ("All files", "*.*"),
            ],
        )

        if not file_path:
            return

        try:
            image = Image.open(file_path)
            self.open_ocr_column_selector(image)
        except Exception as e:
            messagebox.showerror("Image Error", str(e))

    def paste_image_ocr(self):
        try:
            image = ImageGrab.grabclipboard()

            if image is None:
                messagebox.showwarning("No image", "No image found in clipboard.")
                return

            if isinstance(image, list):
                if not image:
                    messagebox.showwarning("No image", "No image found in clipboard.")
                    return
                image = Image.open(image[0])

            if not isinstance(image, Image.Image):
                messagebox.showwarning("No image", "Clipboard content is not an image.")
                return

            self.open_ocr_column_selector(image)

        except Exception as e:
            messagebox.showerror("OCR Error", str(e))

    def open_ocr_column_selector(self, image: Image.Image):
        OCRColumnSelectorWindow(
            self.root,
            image,
            on_text=self.load_ocr_text,
            auto_ocr_func=run_ocr_on_image,
        )

    def load_ocr_text(self, text: str):
        if not text:
            messagebox.showwarning("OCR result empty", "No text was detected.")
            return

        self.input_text.delete("1.0", "end")
        self.input_text.insert("1.0", text)

        ParsePreviewWindow(
            self.root,
            parse_formula_text_advanced(text),
            self.apply_parsed_rows,
        )

    def clear_all(self):
        self.input_text.delete("1.0", "end")

        self.formula_name_var.set("Untitled Formula")
        self.formula_date_var.set(datetime.now().strftime("%Y-%m-%d"))
        self.set_description_value("")

        self.rows = []
        self.current_output_rows = []

        self._sort_column = None
        self._sort_reverse = False

        self.refresh_table([])

        for var in [
            self.current_dilution_var,
            self.force_concentrated_var,
            self.additional_solvent_var,
            self.net_weight_var,
            self.total_parts_var,
        ]:
            var.set("-")

    def calculate(self):
        if not self.rows:
            self.refresh_table([])
            return

        try:
            result = PerfumeCalculator(self.rows, self.get_inputs()).calculate()
        except ValueError as e:
            messagebox.showerror("Input error", str(e))
            return

        self.total_parts_var.set(f"{result['total_parts']:g}")
        self.force_concentrated_var.set(str(result["force_n"]))
        self.current_dilution_var.set(f"{result['current_possible']:.9g}")

        add = result["additional_solvent"]
        self.additional_solvent_var.set(
            add if isinstance(add, str) else f"{add:.9g}"
        )

        self.net_weight_var.set(f"{result['net_weight']:.9g}")

        self.current_output_rows = result["rows"]
        self.refresh_table(result["rows"])

    def get_display_headers(self):
        headers = list(self.HEADERS)

        if self._sort_column is not None:
            if 0 <= self._sort_column < len(headers):
                arrow = " ▼" if self._sort_reverse else " ▲"
                headers[self._sort_column] = headers[self._sort_column] + arrow

        return headers

    def sort_value_for_column(self, row: dict, col: int):
        if col == self.COL_MATERIAL:
            return str(row.get("material", "")).strip().lower()

        if col == self.COL_PART:
            return safe_float(row.get("part"), 0.0)

        if col == self.COL_RAW_PCT:
            return safe_float(row.get("raw_pct"), 0.0)

        if col == self.COL_MANUAL_DILUTION:
            manual = row.get("manual_dilution", "")
            value = safe_float(manual, None)
            return -1.0 if value is None else value

        if col == self.COL_APPLIED_DILUTION:
            return safe_float(row.get("applied_dilution"), 0.0)

        if col == self.COL_WEIGHT:
            return safe_float(row.get("weight"), 0.0)

        return 0.0

    def get_sorted_output_rows(self, output_rows):
        rows = list(output_rows)

        if self._sort_column is None:
            return rows

        try:
            rows.sort(
                key=lambda r: self.sort_value_for_column(r, self._sort_column),
                reverse=self._sort_reverse,
            )
        except Exception:
            pass

        return rows

    def extract_column_from_event(self, event):
        if event is None:
            return None

        if isinstance(event, dict):
            for key in ["column", "col", "c"]:
                if key in event:
                    try:
                        return int(event[key])
                    except Exception:
                        pass

            selected = event.get("selected")
            col = self.extract_column_from_selected(selected)
            if col is not None:
                return col

        for attr in ["column", "col", "c"]:
            if hasattr(event, attr):
                try:
                    return int(getattr(event, attr))
                except Exception:
                    pass

        if hasattr(event, "selected"):
            col = self.extract_column_from_selected(getattr(event, "selected"))
            if col is not None:
                return col

        try:
            selected = self.sheet.get_currently_selected()
            col = self.extract_column_from_selected(selected)
            if col is not None:
                return col
        except Exception:
            pass

        return None

    def extract_column_from_selected(self, selected):
        if selected is None:
            return None

        if hasattr(selected, "column"):
            try:
                return int(selected.column)
            except Exception:
                pass

        if hasattr(selected, "col"):
            try:
                return int(selected.col)
            except Exception:
                pass

        if isinstance(selected, dict):
            for key in ["column", "col", "c"]:
                if key in selected:
                    try:
                        return int(selected[key])
                    except Exception:
                        pass

        if isinstance(selected, (tuple, list)):
            for value in reversed(selected):
                try:
                    ivalue = int(value)
                    if 0 <= ivalue < len(self.HEADERS):
                        return ivalue
                except Exception:
                    continue

        return None

    def on_column_select_for_sort(self, event=None):
        if self._refreshing_table:
            return

        col = self.extract_column_from_event(event)

        if col is None:
            return

        if col < 0 or col >= len(self.HEADERS):
            return

        if self._sort_column == col:
            self._sort_reverse = not self._sort_reverse
        else:
            self._sort_column = col
            self._sort_reverse = False

        self.refresh_table(self.current_output_rows)

    def refresh_table(self, output_rows):
        display_rows = self.get_sorted_output_rows(output_rows)

        data = []

        for r in display_rows:
            data.append(
                [
                    r["material"],
                    f"{r['part']:g}",
                    f"{r['raw_pct']:.6f}",
                    r["manual_dilution"],
                    f"{r['applied_dilution']:.6g}",
                    f"{r['weight']:.6f}",
                ]
            )

        self._refreshing_table = True

        try:
            self.sheet.set_sheet_data(data)
            self.sheet.headers(self.get_display_headers())
            self.style_sheet()
            self.sheet.dehighlight_all()

            for idx, row in enumerate(display_rows):
                if row.get("part_adjusted_by_dilution", False):
                    self.sheet.highlight_cells(
                        row=idx,
                        column=self.COL_PART,
                        bg="#dbeafe",
                        fg="#1d4ed8",
                    )

                if row.get("topn_changed", False):
                    self.sheet.highlight_cells(
                        row=idx,
                        column=self.COL_APPLIED_DILUTION,
                        bg="#fee2e2",
                        fg="#b91c1c",
                    )

            self.sheet.refresh()

        finally:
            self._refreshing_table = False

    def on_sheet_edit(self, event=None):
        if self._refreshing_table:
            return

        try:
            data = self.sheet.get_sheet_data()
        except Exception:
            return

        new_rows = []

        for row in data:
            if len(row) < 6:
                continue

            material = str(row[self.COL_MATERIAL]).strip()
            part = safe_float(row[self.COL_PART], None)
            manual = str(row[self.COL_MANUAL_DILUTION]).strip()

            if not material and part is None:
                continue

            if part is None or part <= 0:
                continue

            if manual:
                d = safe_float(manual, None)
                if d is None or d <= 0:
                    manual = ""

            new_rows.append(
                {
                    "material": material,
                    "part": part,
                    "manual_dilution": manual,
                    "part_adjusted_by_dilution": False,
                    "parsed_part": part,
                    "parsed_dilution": manual,
                }
            )

        if new_rows:
            self.rows = new_rows
            self.root.after(50, self.calculate)