import re
import tkinter as tk
from tkinter import ttk, messagebox

from PIL import Image, ImageTk, ImageOps, ImageEnhance, ImageFilter

try:
    import pytesseract
except ImportError:
    pytesseract = None

try:
    import cv2
    import numpy as np
except ImportError:
    cv2 = None
    np = None

from .ocr import ensure_tesseract_ready, clean_ocr_text


# ------------------------------------------------------------
# Text cleanup
# ------------------------------------------------------------

def normalize_spaces(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def clean_numeric_text(text: str) -> str:
    text = str(text or "").strip()
    text = text.replace(",", ".")
    text = text.replace("％", "%")

    replacements = {
        "O": "0",
        "o": "0",
        "I": "1",
        "l": "1",
        "S": "5",
        "s": "5",
        "B": "8",
    }

    out = ""
    for ch in text:
        out += replacements.get(ch, ch)

    return out.strip()


def extract_number(text: str) -> str:
    text = clean_numeric_text(text)
    m = re.search(r"\d+(?:\.\d+)?", text)
    return m.group(0) if m else ""


def extract_percent(text: str) -> str:
    text = clean_numeric_text(text)
    text = text.replace("%", "")

    m = re.search(r"\d+(?:\.\d+)?", text)
    if not m:
        return ""

    value = m.group(0)

    try:
        v = float(value)
        if v <= 0 or v > 100:
            return ""
    except Exception:
        return ""

    return value


def split_material_and_dilution_from_text(text: str) -> tuple[str, str]:
    """
    Accepts:
      Calone 10%
      Aurantiol Ultra (100%)
      AmbroxSuper10%

    Returns:
      material, dilution
    """
    text = normalize_spaces(text)
    text = text.replace("％", "%")

    # Parenthesized dilution: Material (10%)
    m = re.search(r"\((\d+(?:\.\d+)?)\s*%\)", text)
    if m:
        dilution = m.group(1)
        material = (text[:m.start()] + text[m.end():]).strip()
        material = material.strip(" -–—,;:")
        return normalize_spaces(material), dilution

    # Normal dilution: Material 10%
    m = re.search(r"(?<!\w)(\d+(?:\.\d+)?)\s*%", text)
    if m:
        dilution = m.group(1)
        material = (text[:m.start()] + text[m.end():]).strip()
        material = material.strip(" -–—,;:")
        return normalize_spaces(material), dilution

    return text, ""


def clean_material_text(text: str) -> str:
    text = str(text or "").strip()

    replacements = {
        "\r": "",
        "\n": " ",
        "|": "",
        "—": "-",
        "–": "-",
        "“": '"',
        "”": '"',
        "‘": "'",
        "’": "'",
        "\u00a0": " ",
        "％": "%",
    }

    for old, new in replacements.items():
        text = text.replace(old, new)

    text = normalize_spaces(text)
    text = text.strip(" -–—,;:")

    # Split accidental camelcase.
    text = re.sub(r"([a-z])([A-Z])", r"\1 \2", text)

    compact_fixes = {
        "ISOESuper": "ISO E Super",
        "IsoESuper": "Iso E Super",
        "AmbroxSuper": "Ambrox Super",
        "SechuanPepper": "Sechuan Pepper",
        "Jasmineacc": "Jasmine acc",
        "Dihydromyrcenol": "Dihydromyrcenol",
        "VioletLeafAbsolute": "Violet Leaf Absolute",
        "EthylLinalol": "Ethyl linalol",
        "EthylLinalool": "Ethyl linalool",
        "LemonOil": "Lemon Oil",
        "RoseEssence": "Rose Essence",
    }

    compact = text.replace(" ", "")
    if compact in compact_fixes:
        text = compact_fixes[compact]

    return normalize_spaces(text)


def is_bad_material_row(text: str) -> bool:
    low = normalize_spaces(text).lower()
    low_clean = re.sub(r"[^a-z0-9% ]+", " ", low)
    low_clean = normalize_spaces(low_clean)

    if not low_clean:
        return True

    bad_exact = {
        "ingredient",
        "material",
        "formula entry",
        "formula",
        "entry",
        "total",
        "amount",
        "percentage",
        "weight",
        "part",
        "a",
        "i",
        "l",
        "1",
    }

    if low_clean in bad_exact:
        return True

    if "ingredient" in low_clean:
        return True

    if "formula entry" in low_clean:
        return True

    if low_clean.startswith("total"):
        return True

    if "perfumer" in low_clean and "alcohol" in low_clean:
        return True

    if not re.search(r"[A-Za-z가-힣]", text):
        return True

    if len(text.strip()) <= 1:
        return True

    return False


# ------------------------------------------------------------
# OCR preprocessing
# ------------------------------------------------------------

def preprocess_cell_for_ocr(image: Image.Image, kind: str) -> Image.Image:
    """
    OCR one table cell.
    """
    image = image.convert("RGB")

    # Remove border leftovers by painting the outer edge white.
    w, h = image.size
    if w > 4 and h > 4:
        px = image.load()
        edge = max(1, min(w, h) // 30)

        for x in range(w):
            for y in range(edge):
                px[x, y] = (255, 255, 255)
                px[x, h - 1 - y] = (255, 255, 255)

        for y in range(h):
            for x in range(edge):
                px[x, y] = (255, 255, 255)
                px[w - 1 - x, y] = (255, 255, 255)

    # Add white padding.
    pad_x = max(8, int(w * 0.08))
    pad_y = max(5, int(h * 0.18))

    padded = Image.new("RGB", (w + pad_x * 2, h + pad_y * 2), "white")
    padded.paste(image, (pad_x, pad_y))
    image = padded

    w, h = image.size
    scale = 5 if kind in {"number", "percent"} else 4

    image = image.resize((w * scale, h * scale), Image.Resampling.LANCZOS)
    image = image.convert("L")
    image = ImageOps.autocontrast(image)
    image = image.filter(ImageFilter.MedianFilter(size=3))
    image = ImageEnhance.Contrast(image).enhance(2.2)
    image = ImageEnhance.Sharpness(image).enhance(2.0)

    # Softer threshold than before. The previous hard threshold often erased thin digits.
    image = image.point(lambda p: 255 if p > 175 else 0)

    return image


def preprocess_column_for_data_ocr(image: Image.Image, kind: str) -> Image.Image:
    """
    Fallback OCR for entire selected column.
    """
    image = image.convert("RGB")

    w, h = image.size
    scale = 4 if kind in {"number", "percent"} else 3

    image = image.resize((w * scale, h * scale), Image.Resampling.LANCZOS)
    image = image.convert("L")
    image = ImageOps.autocontrast(image)
    image = image.filter(ImageFilter.MedianFilter(size=3))
    image = ImageEnhance.Contrast(image).enhance(2.0)
    image = ImageEnhance.Sharpness(image).enhance(1.8)
    image = image.point(lambda p: 255 if p > 175 else 0)

    return image


def whitelist_for_kind(kind: str) -> str:
    if kind == "number":
        return "0123456789.,"
    if kind == "percent":
        return "0123456789.,%"
    return (
        "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
        "abcdefghijklmnopqrstuvwxyz"
        "0123456789"
        " .,%()-_'\"®™/&:+"
    )


def score_ocr_text(text: str, kind: str) -> int:
    text = normalize_spaces(text)

    if not text:
        return -999

    score = 0

    if kind == "number":
        if re.search(r"\d", text):
            score += 50
        if re.fullmatch(r"[0-9.,]+", text):
            score += 80
        if re.fullmatch(r"\d+(?:[.,]\d+)?", text):
            score += 120
        score -= len(re.sub(r"[0-9.,]", "", text)) * 20
        return score

    if kind == "percent":
        if re.search(r"\d", text):
            score += 50
        if "%" in text:
            score += 25
        if re.fullmatch(r"[0-9.,% ]+", text):
            score += 60
        if re.search(r"\d+(?:[.,]\d+)?\s*%?", text):
            score += 80
        score -= len(re.sub(r"[0-9.,% ]", "", text)) * 20
        return score

    letters = len(re.findall(r"[A-Za-z가-힣]", text))
    score += letters * 10

    if len(text) >= 2:
        score += 20

    if text.lower() in {"a", "i", "l", "1"}:
        score -= 100

    if re.search(r"[|_=~{}\[\]]", text):
        score -= 50

    return score


def ocr_single_cell(image: Image.Image, kind: str) -> str:
    """
    OCR one cell using multiple PSM modes and return best result.
    """
    if pytesseract is None:
        raise RuntimeError(
            "pytesseract is not installed.\n\n"
            "Run:\n"
            "pip install pytesseract pillow"
        )

    ensure_tesseract_ready()

    processed = preprocess_cell_for_ocr(image, kind)
    whitelist = whitelist_for_kind(kind)

    best_text = ""
    best_score = -999999

    for psm in [7, 8, 13, 6]:
        config = (
            f"--oem 3 --psm {psm} "
            f"-c tessedit_char_whitelist={whitelist} "
            "-c preserve_interword_spaces=1"
        )

        try:
            text = pytesseract.image_to_string(
                processed,
                lang="eng",
                config=config,
            )
        except Exception:
            continue

        text = clean_ocr_text(text)
        text = text.replace("\n", " ")
        text = normalize_spaces(text)

        score = score_ocr_text(text, kind)

        if score > best_score:
            best_score = score
            best_text = text

    best_text = normalize_spaces(best_text)

    if kind == "number":
        return extract_number(best_text)

    if kind == "percent":
        return extract_percent(best_text)

    return clean_material_text(best_text)


# ------------------------------------------------------------
# Row segmentation from table lines
# ------------------------------------------------------------

def pil_to_cv_gray(image: Image.Image):
    if cv2 is None or np is None:
        return None

    rgb = image.convert("RGB")
    arr = np.array(rgb)
    gray = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY)
    return gray


def merge_positions(positions, max_gap=5):
    if not positions:
        return []

    positions = sorted(int(p) for p in positions)
    groups = []

    start = positions[0]
    prev = positions[0]

    for p in positions[1:]:
        if p - prev > max_gap:
            groups.append((start, prev))
            start = p
        prev = p

    groups.append((start, prev))

    return [int((a + b) / 2) for a, b in groups]


def detect_horizontal_lines(image: Image.Image) -> list[int]:
    """
    Detect table row lines inside selected column crop.
    """
    if cv2 is None or np is None:
        return []

    gray = pil_to_cv_gray(image)
    if gray is None:
        return []

    h, w = gray.shape[:2]

    # Adaptive threshold: table lines become white.
    bw = cv2.adaptiveThreshold(
        gray,
        255,
        cv2.ADAPTIVE_THRESH_MEAN_C,
        cv2.THRESH_BINARY_INV,
        25,
        12,
    )

    kernel_len = max(12, int(w * 0.45))
    horizontal_kernel = cv2.getStructuringElement(
        cv2.MORPH_RECT,
        (kernel_len, 1),
    )

    horizontal = cv2.morphologyEx(
        bw,
        cv2.MORPH_OPEN,
        horizontal_kernel,
        iterations=1,
    )

    projection = np.sum(horizontal > 0, axis=1)

    # Loose threshold because selected crop can be narrow.
    raw = np.where(projection > w * 0.18)[0].tolist()

    lines = merge_positions(raw, max_gap=4)

    # Add bounds when selection starts/ends near table border.
    if lines:
        if lines[0] > 12:
            lines = [0] + lines
        if h - lines[-1] > 12:
            lines.append(h - 1)

    # Filter weird tiny gaps.
    filtered = []
    for y in lines:
        if not filtered:
            filtered.append(y)
            continue

        if y - filtered[-1] >= 10:
            filtered.append(y)

    return filtered


def rows_from_line_segments(image: Image.Image, kind: str) -> list[dict]:
    """
    Best path for bordered tables.
    Finds row bands using horizontal lines and OCRs each cell separately.
    """
    y_lines = detect_horizontal_lines(image)

    if len(y_lines) < 3:
        return []

    w, h = image.size
    rows = []

    for i in range(len(y_lines) - 1):
        y1 = y_lines[i]
        y2 = y_lines[i + 1]

        row_h = y2 - y1

        if row_h < 10:
            continue

        # Crop inside row borders.
        pad_y = max(2, int(row_h * 0.12))
        cy1 = max(0, y1 + pad_y)
        cy2 = min(h, y2 - pad_y)

        if cy2 <= cy1:
            continue

        cell = image.crop((0, cy1, w, cy2))
        text = ocr_single_cell(cell, kind)

        if not text:
            continue

        y_ratio = ((y1 + y2) / 2) / max(h, 1)

        if kind == "number":
            text = extract_number(text)
            if not text:
                continue

        elif kind == "percent":
            text = extract_percent(text)
            if not text:
                continue

        else:
            text = clean_material_text(text)
            if is_bad_material_row(text):
                continue

        rows.append(
            {
                "y_ratio": y_ratio,
                "text": text,
            }
        )

    return rows


# ------------------------------------------------------------
# Fallback column OCR using image_to_data
# ------------------------------------------------------------

def rows_from_data_ocr(image: Image.Image, kind: str) -> list[dict]:
    """
    Fallback when row-line segmentation fails.
    OCRs the whole column and groups words by y coordinate.
    """
    if pytesseract is None:
        raise RuntimeError(
            "pytesseract is not installed.\n\n"
            "Run:\n"
            "pip install pytesseract pillow"
        )

    ensure_tesseract_ready()

    processed = preprocess_column_for_data_ocr(image, kind)
    pw, ph = processed.size

    whitelist = whitelist_for_kind(kind)

    best_rows = []

    for psm in [6, 4, 11]:
        config = (
            f"--oem 3 --psm {psm} "
            f"-c tessedit_char_whitelist={whitelist} "
            "-c preserve_interword_spaces=1"
        )

        try:
            data = pytesseract.image_to_data(
                processed,
                lang="eng",
                config=config,
                output_type=pytesseract.Output.DICT,
            )
        except Exception:
            continue

        words = []

        n = len(data.get("text", []))

        for i in range(n):
            text = str(data["text"][i] or "").strip()
            text = clean_ocr_text(text).replace("\n", " ")
            text = normalize_spaces(text)

            if not text:
                continue

            try:
                conf = float(data["conf"][i])
            except Exception:
                conf = -1

            if conf < -1:
                continue

            left = int(data["left"][i])
            top = int(data["top"][i])
            width = int(data["width"][i])
            height = int(data["height"][i])

            words.append(
                {
                    "text": text,
                    "left": left,
                    "top": top,
                    "width": width,
                    "height": height,
                    "center_y": top + height / 2,
                }
            )

        if not words:
            continue

        heights = sorted([w["height"] for w in words if w["height"] > 0])
        median_h = heights[len(heights) // 2] if heights else 20
        y_threshold = max(12, median_h * 0.8)

        words = sorted(words, key=lambda w: (w["center_y"], w["left"]))
        grouped = []

        for word in words:
            placed = False

            for row in grouped:
                row_y = sum(w["center_y"] for w in row) / len(row)
                if abs(word["center_y"] - row_y) <= y_threshold:
                    row.append(word)
                    placed = True
                    break

            if not placed:
                grouped.append([word])

        rows = []

        for row in grouped:
            row = sorted(row, key=lambda w: w["left"])
            text = normalize_spaces(" ".join(w["text"] for w in row))

            if not text:
                continue

            y_center = sum(w["center_y"] for w in row) / len(row)
            y_ratio = y_center / max(ph, 1)

            if kind == "number":
                text = extract_number(text)
                if not text:
                    continue

            elif kind == "percent":
                text = extract_percent(text)
                if not text:
                    continue

            else:
                text = clean_material_text(text)
                if is_bad_material_row(text):
                    continue

            rows.append(
                {
                    "y_ratio": y_ratio,
                    "text": text,
                }
            )

        if len(rows) > len(best_rows):
            best_rows = rows

    return best_rows


def ocr_crop_to_rows(image: Image.Image, kind: str) -> list[dict]:
    """
    Main column OCR function.

    First tries row segmentation using table lines.
    If that fails, falls back to image_to_data word grouping.
    """
    rows = rows_from_line_segments(image, kind)

    if len(rows) >= 2:
        return rows

    return rows_from_data_ocr(image, kind)


def nearest_row_by_y(base_y: float, rows: list[dict], max_distance: float = 0.06):
    if not rows:
        return None

    best = None
    best_dist = 999

    for row in rows:
        dist = abs(float(row["y_ratio"]) - float(base_y))
        if dist < best_dist:
            best = row
            best_dist = dist

    if best is None:
        return None

    if best_dist > max_distance:
        return None

    return best


# ------------------------------------------------------------
# GUI
# ------------------------------------------------------------

class OCRColumnSelectorWindow:
    """
    Lets the user drag-select Material / Dilution / Part columns,
    then OCRs each crop separately for better accuracy.
    """

    COLORS = {
        "material": "#22c55e",
        "dilution": "#f59e0b",
        "part": "#3b82f6",
    }

    LABELS = {
        "material": "Material",
        "dilution": "Dilution",
        "part": "Part / Amount",
    }

    def __init__(self, parent, image: Image.Image, on_text, auto_ocr_func=None):
        self.parent = parent
        self.image = image.convert("RGB")
        self.on_text = on_text
        self.auto_ocr_func = auto_ocr_func

        self.window = tk.Toplevel(parent)
        self.window.title("Column OCR")
        self.window.geometry("1180x820")
        self.window.transient(parent)
        self.window.grab_set()

        self.mode = tk.StringVar(value="material")

        self.rectangles = {
            "material": None,
            "dilution": None,
            "part": None,
        }

        self.canvas_rect_ids = {
            "material": None,
            "dilution": None,
            "part": None,
        }

        self.drag_start = None
        self.temp_rect_id = None

        self.display_scale = 1.0
        self.display_image = None
        self.tk_image = None

        self.build_ui()
        self.prepare_image()
        self.draw_image()

    def build_ui(self):
        top = ttk.Frame(self.window, padding=8)
        top.pack(fill="x")

        ttk.Label(
            top,
            text=(
                "Drag the column areas. Required: Material and Part. "
                "Dilution is optional. Select full column height including rows."
            ),
            font=("Segoe UI", 10, "bold"),
        ).pack(side="left")

        button_row = ttk.Frame(self.window, padding=(8, 0, 8, 8))
        button_row.pack(fill="x")

        ttk.Radiobutton(
            button_row,
            text="1. Material column",
            variable=self.mode,
            value="material",
        ).pack(side="left", padx=(0, 10))

        ttk.Radiobutton(
            button_row,
            text="2. Dilution column optional",
            variable=self.mode,
            value="dilution",
        ).pack(side="left", padx=(0, 10))

        ttk.Radiobutton(
            button_row,
            text="3. Part / Amount column",
            variable=self.mode,
            value="part",
        ).pack(side="left", padx=(0, 10))

        ttk.Button(
            button_row,
            text="Run Column OCR",
            command=self.run_column_ocr,
        ).pack(side="right", padx=(5, 0))

        ttk.Button(
            button_row,
            text="Use OCR Result",
            command=self.use_result,
        ).pack(side="right", padx=(5, 0))

        if self.auto_ocr_func is not None:
            ttk.Button(
                button_row,
                text="Auto OCR",
                command=self.run_auto_ocr,
            ).pack(side="right", padx=(5, 0))

        ttk.Button(
            button_row,
            text="Clear Selections",
            command=self.clear_selections,
        ).pack(side="right", padx=(5, 0))

        ttk.Button(
            button_row,
            text="Cancel",
            command=self.window.destroy,
        ).pack(side="right", padx=(5, 0))

        main = ttk.Frame(self.window, padding=8)
        main.pack(fill="both", expand=True)

        canvas_frame = ttk.Frame(main)
        canvas_frame.pack(side="left", fill="both", expand=True, padx=(0, 8))

        self.canvas = tk.Canvas(canvas_frame, bg="#111827", cursor="crosshair")
        self.canvas.pack(fill="both", expand=True)

        self.canvas.bind("<ButtonPress-1>", self.on_mouse_down)
        self.canvas.bind("<B1-Motion>", self.on_mouse_drag)
        self.canvas.bind("<ButtonRelease-1>", self.on_mouse_up)

        right = ttk.Frame(main)
        right.pack(side="right", fill="both", expand=False)

        ttk.Label(
            right,
            text="OCR normalized result",
            font=("Segoe UI", 10, "bold"),
        ).pack(anchor="w")

        self.result_text = tk.Text(right, width=42, height=34, wrap="none")
        self.result_text.pack(fill="both", expand=True, pady=(5, 0))

        help_text = (
            "Output format:\n"
            "Material Dilution% Part\n\n"
            "Examples:\n"
            "Calone 10% 10\n"
            "Geosmin 1% 3.5\n"
            "Hedione 400\n\n"
            "After Use OCR Result,\n"
            "Parse Preview will open."
        )

        ttk.Label(
            right,
            text=help_text,
            justify="left",
            foreground="#374151",
        ).pack(anchor="w", pady=(8, 0))

    def prepare_image(self):
        max_w = 850
        max_h = 680

        w, h = self.image.size
        scale = min(max_w / max(w, 1), max_h / max(h, 1), 1.0)

        if w < 700:
            scale = min(1.8, 900 / max(w, 1))

        self.display_scale = scale

        new_w = int(w * scale)
        new_h = int(h * scale)

        self.display_image = self.image.resize(
            (new_w, new_h),
            Image.Resampling.LANCZOS,
        )

        self.tk_image = ImageTk.PhotoImage(self.display_image)

    def draw_image(self):
        self.canvas.delete("all")
        self.canvas.create_image(0, 0, anchor="nw", image=self.tk_image)
        self.canvas.config(
            scrollregion=(
                0,
                0,
                self.display_image.size[0],
                self.display_image.size[1],
            )
        )
        self.redraw_rectangles()

    def redraw_rectangles(self):
        for mode, rect_id in self.canvas_rect_ids.items():
            if rect_id is not None:
                self.canvas.delete(rect_id)
                self.canvas_rect_ids[mode] = None

        for mode, rect in self.rectangles.items():
            if rect is None:
                continue

            x1, y1, x2, y2 = rect
            color = self.COLORS[mode]

            rect_id = self.canvas.create_rectangle(
                x1,
                y1,
                x2,
                y2,
                outline=color,
                width=3,
            )

            self.canvas.create_text(
                x1 + 6,
                y1 + 6,
                anchor="nw",
                fill=color,
                font=("Segoe UI", 10, "bold"),
                text=self.LABELS[mode],
            )

            self.canvas_rect_ids[mode] = rect_id

    def clear_selections(self):
        self.rectangles = {
            "material": None,
            "dilution": None,
            "part": None,
        }
        self.draw_image()

    def on_mouse_down(self, event):
        self.drag_start = (event.x, event.y)

        if self.temp_rect_id is not None:
            self.canvas.delete(self.temp_rect_id)
            self.temp_rect_id = None

    def on_mouse_drag(self, event):
        if self.drag_start is None:
            return

        x1, y1 = self.drag_start
        x2, y2 = event.x, event.y

        if self.temp_rect_id is not None:
            self.canvas.delete(self.temp_rect_id)

        mode = self.mode.get()
        color = self.COLORS.get(mode, "#ffffff")

        self.temp_rect_id = self.canvas.create_rectangle(
            x1,
            y1,
            x2,
            y2,
            outline=color,
            width=2,
            dash=(4, 3),
        )

    def on_mouse_up(self, event):
        if self.drag_start is None:
            return

        x1, y1 = self.drag_start
        x2, y2 = event.x, event.y

        self.drag_start = None

        if self.temp_rect_id is not None:
            self.canvas.delete(self.temp_rect_id)
            self.temp_rect_id = None

        x1, x2 = sorted([x1, x2])
        y1, y2 = sorted([y1, y2])

        if abs(x2 - x1) < 10 or abs(y2 - y1) < 10:
            return

        mode = self.mode.get()
        self.rectangles[mode] = (x1, y1, x2, y2)

        self.draw_image()

    def display_rect_to_image_crop(self, rect):
        """
        Convert displayed canvas rectangle to original image crop.
        """
        if rect is None:
            return None

        x1, y1, x2, y2 = rect
        scale = self.display_scale

        ox1 = int(x1 / scale)
        oy1 = int(y1 / scale)
        ox2 = int(x2 / scale)
        oy2 = int(y2 / scale)

        w, h = self.image.size

        ox1 = max(0, min(w, ox1))
        ox2 = max(0, min(w, ox2))
        oy1 = max(0, min(h, oy1))
        oy2 = max(0, min(h, oy2))

        if ox2 <= ox1 or oy2 <= oy1:
            return None

        return self.image.crop((ox1, oy1, ox2, oy2))

    def run_column_ocr(self):
        material_rect = self.rectangles.get("material")
        part_rect = self.rectangles.get("part")
        dilution_rect = self.rectangles.get("dilution")

        if material_rect is None:
            messagebox.showwarning(
                "Missing Material column",
                "Drag-select the Material column first.",
            )
            return

        if part_rect is None:
            messagebox.showwarning(
                "Missing Part column",
                "Drag-select the Part / Amount column first.",
            )
            return

        try:
            material_crop = self.display_rect_to_image_crop(material_rect)
            part_crop = self.display_rect_to_image_crop(part_rect)
            dilution_crop = (
                self.display_rect_to_image_crop(dilution_rect)
                if dilution_rect
                else None
            )

            if material_crop is None or part_crop is None:
                messagebox.showerror(
                    "Invalid selection",
                    "Column selection is invalid.",
                )
                return

            material_rows = ocr_crop_to_rows(material_crop, "text")
            part_rows = ocr_crop_to_rows(part_crop, "number")
            dilution_rows = (
                ocr_crop_to_rows(dilution_crop, "percent")
                if dilution_crop
                else []
            )

            print("=== COLUMN OCR DEBUG ===")
            print("material_rows:", material_rows)
            print("dilution_rows:", dilution_rows)
            print("part_rows:", part_rows)
            print("========================")

            lines = []

            for material_row in material_rows:
                material_text = material_row["text"]
                y = material_row["y_ratio"]

                part_match = nearest_row_by_y(y, part_rows)
                if part_match is None:
                    continue

                part = extract_number(part_match["text"])
                if not part:
                    continue

                material_text = clean_material_text(material_text)

                material_without_dil, material_dilution = split_material_and_dilution_from_text(
                    material_text
                )

                dilution = ""

                if dilution_rows:
                    dilution_match = nearest_row_by_y(y, dilution_rows)
                    if dilution_match is not None:
                        dilution = extract_percent(dilution_match["text"])

                if not dilution and material_dilution:
                    dilution = material_dilution

                material = material_without_dil if material_without_dil else material_text
                material = clean_material_text(material)

                if is_bad_material_row(material):
                    continue

                if dilution:
                    lines.append(f"{material} {dilution}% {part}")
                else:
                    lines.append(f"{material} {part}")

            text = "\n".join(lines).strip()

            self.result_text.delete("1.0", "end")
            self.result_text.insert("1.0", text)

            if not text:
                messagebox.showwarning(
                    "Column OCR empty",
                    (
                        "No usable rows were detected.\n\n"
                        "Try selecting the columns from just above the first data row "
                        "to just below the last data row, or make the selection slightly wider."
                    ),
                )

        except Exception as e:
            messagebox.showerror("Column OCR Error", str(e))

    def run_auto_ocr(self):
        if self.auto_ocr_func is None:
            return

        try:
            text = self.auto_ocr_func(self.image)

            self.result_text.delete("1.0", "end")
            self.result_text.insert("1.0", text)

            if not text.strip():
                messagebox.showwarning("Auto OCR empty", "No text was detected.")

        except Exception as e:
            messagebox.showerror("Auto OCR Error", str(e))

    def use_result(self):
        text = self.result_text.get("1.0", "end").strip()

        if not text:
            messagebox.showwarning(
                "No OCR result",
                "Run Column OCR or Auto OCR first.",
            )
            return

        self.on_text(text)
        self.window.destroy()