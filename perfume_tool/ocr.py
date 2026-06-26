import os
import sys
import shutil
import re
from dataclasses import dataclass
from typing import List, Tuple, Optional

from PIL import Image, ImageGrab, ImageOps, ImageEnhance, ImageFilter

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


@dataclass
class OCRWord:
    text: str
    left: int
    top: int
    width: int
    height: int
    conf: float

    @property
    def right(self) -> int:
        return self.left + self.width

    @property
    def center_x(self) -> float:
        return self.left + self.width / 2

    @property
    def center_y(self) -> float:
        return self.top + self.height / 2


# ------------------------------------------------------------
# Tesseract setup
# ------------------------------------------------------------

def resource_path(relative_path: str) -> str:
    """
    Works both when running as normal Python and inside PyInstaller portable build.
    """
    try:
        base_path = sys._MEIPASS  # type: ignore[attr-defined]
    except AttributeError:
        base_path = os.path.abspath(".")

    return os.path.join(base_path, relative_path)


def configure_tesseract() -> bool:
    """
    Looks for bundled Tesseract first, then system PATH, then common Windows install folders.

    Expected bundled structure:
      tesseract/
        tesseract.exe
        tessdata/
          eng.traineddata
        *.dll
    """
    if pytesseract is None:
        return False

    bundled_tesseract = resource_path(os.path.join("tesseract", "tesseract.exe"))
    bundled_tessdata = resource_path(os.path.join("tesseract", "tessdata"))

    if os.path.exists(bundled_tesseract):
        pytesseract.pytesseract.tesseract_cmd = bundled_tesseract
        os.environ["TESSDATA_PREFIX"] = bundled_tessdata
        return True

    existing = shutil.which("tesseract")
    if existing:
        pytesseract.pytesseract.tesseract_cmd = existing
        tessdata = os.path.join(os.path.dirname(existing), "tessdata")
        if os.path.isdir(tessdata):
            os.environ["TESSDATA_PREFIX"] = tessdata
        return True

    common_paths = [
        r"C:\Program Files\Tesseract-OCR\tesseract.exe",
        r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
    ]

    for path in common_paths:
        if os.path.exists(path):
            pytesseract.pytesseract.tesseract_cmd = path
            tessdata = os.path.join(os.path.dirname(path), "tessdata")
            if os.path.isdir(tessdata):
                os.environ["TESSDATA_PREFIX"] = tessdata
            return True

    return False


def ensure_tesseract_ready() -> None:
    if pytesseract is None:
        raise RuntimeError(
            "pytesseract is not installed.\n\n"
            "Run:\n"
            "pip install pytesseract pillow"
        )

    if not configure_tesseract():
        raise RuntimeError(
            "Tesseract OCR engine was not found.\n\n"
            "For bundled portable build, put this next to run.py before building:\n"
            "tesseract/tesseract.exe\n"
            "tesseract/tessdata/eng.traineddata\n\n"
            "Or install Tesseract OCR and add it to PATH."
        )


# ------------------------------------------------------------
# Generic cleaning / preprocessing
# ------------------------------------------------------------

def clean_ocr_token(text: str) -> str:
    text = str(text or "").strip()

    replacements = {
        "\r": "",
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

    return text.strip()


def clean_ocr_text(text: str) -> str:
    if not text:
        return ""

    replacements = {
        "\r": "",
        "|": " ",
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

    lines = []
    for line in text.splitlines():
        line = line.strip()
        line = " ".join(line.split())
        if line:
            lines.append(line)

    return "\n".join(lines)


def preprocess_image_for_ocr(image: Image.Image) -> Image.Image:
    """
    Used by non-grid OCR path.
    Good for screenshot tables without heavy visible grid lines.
    """
    image = image.convert("RGB")

    w, h = image.size
    scale = 3

    if w * scale > 5500:
        scale = max(1, int(5500 / max(w, 1)))

    image = image.resize((w * scale, h * scale), Image.Resampling.LANCZOS)
    image = image.convert("L")
    image = ImageOps.autocontrast(image)
    image = image.filter(ImageFilter.MedianFilter(size=3))
    image = ImageEnhance.Contrast(image).enhance(2.2)
    image = ImageEnhance.Sharpness(image).enhance(1.8)
    image = image.point(lambda p: 255 if p > 185 else 0)

    return image


def pil_to_cv_bgr(image: Image.Image):
    if cv2 is None or np is None:
        return None

    rgb = image.convert("RGB")
    arr = np.array(rgb)
    return cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)


def normalize_spaces(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


# ------------------------------------------------------------
# Grid table OCR
# Handles bordered tables:
# Ingredient | Percentage | Amount
# ------------------------------------------------------------

def resize_for_grid_detection(img_bgr):
    """
    Upscale small screenshots before grid detection.
    Returns resized image and scale factor.
    """
    if cv2 is None:
        return img_bgr, 1.0

    h, w = img_bgr.shape[:2]

    scale = 1.0

    if w < 900:
        scale = 900 / max(w, 1)
    elif w > 2200:
        scale = 2200 / max(w, 1)

    if abs(scale - 1.0) < 0.01:
        return img_bgr, 1.0

    resized = cv2.resize(
        img_bgr,
        None,
        fx=scale,
        fy=scale,
        interpolation=cv2.INTER_CUBIC if scale > 1 else cv2.INTER_AREA,
    )

    return resized, scale


def merge_close_positions(positions: List[int], max_gap: int = 6) -> List[int]:
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


def find_grid_lines_by_projection(img_bgr) -> Tuple[List[int], List[int]]:
    """
    Detect vertical and horizontal grid lines using morphology + projections.
    """
    if cv2 is None or np is None:
        return [], []

    gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
    h, w = gray.shape[:2]

    # Adaptive threshold handles screenshots with anti-aliased grey grid lines.
    bw = cv2.adaptiveThreshold(
        gray,
        255,
        cv2.ADAPTIVE_THRESH_MEAN_C,
        cv2.THRESH_BINARY_INV,
        25,
        12,
    )

    # Horizontal lines.
    horizontal_kernel_len = max(25, w // 12)
    horizontal_kernel = cv2.getStructuringElement(
        cv2.MORPH_RECT,
        (horizontal_kernel_len, 1),
    )
    horizontal = cv2.morphologyEx(
        bw,
        cv2.MORPH_OPEN,
        horizontal_kernel,
        iterations=1,
    )

    # Vertical lines.
    vertical_kernel_len = max(20, h // 12)
    vertical_kernel = cv2.getStructuringElement(
        cv2.MORPH_RECT,
        (1, vertical_kernel_len),
    )
    vertical = cv2.morphologyEx(
        bw,
        cv2.MORPH_OPEN,
        vertical_kernel,
        iterations=1,
    )

    vertical_projection = np.sum(vertical > 0, axis=0)
    horizontal_projection = np.sum(horizontal > 0, axis=1)

    x_raw = np.where(vertical_projection > h * 0.18)[0].tolist()
    y_raw = np.where(horizontal_projection > w * 0.18)[0].tolist()

    x_lines = merge_close_positions(x_raw, max_gap=8)
    y_lines = merge_close_positions(y_raw, max_gap=8)

    return x_lines, y_lines


def add_bounds_if_needed(lines: List[int], limit: int, tolerance: int = 25) -> List[int]:
    """
    Adds image bounds when table border is close to the image edge
    or when the first/last detected line is missing.
    """
    if not lines:
        return []

    lines = sorted(lines)

    if lines[0] > tolerance:
        lines = [0] + lines

    if limit - lines[-1] > tolerance:
        lines = lines + [limit - 1]

    return sorted(set(lines))


def filter_grid_lines(lines: List[int], min_gap: int = 18) -> List[int]:
    """
    Remove near-duplicate / tiny-gap grid lines.
    """
    if not lines:
        return []

    lines = sorted(lines)
    filtered = [lines[0]]

    for p in lines[1:]:
        if p - filtered[-1] >= min_gap:
            filtered.append(p)
        else:
            filtered[-1] = int((filtered[-1] + p) / 2)

    return filtered


def detect_grid_table(image: Image.Image):
    """
    Returns:
      img_bgr, x_lines, y_lines
    """
    img_bgr = pil_to_cv_bgr(image)

    if img_bgr is None:
        return None, [], []

    img_bgr, _scale = resize_for_grid_detection(img_bgr)
    h, w = img_bgr.shape[:2]

    x_lines, y_lines = find_grid_lines_by_projection(img_bgr)

    x_lines = add_bounds_if_needed(x_lines, w, tolerance=35)
    y_lines = add_bounds_if_needed(y_lines, h, tolerance=35)

    x_lines = filter_grid_lines(x_lines, min_gap=25)
    y_lines = filter_grid_lines(y_lines, min_gap=18)

    return img_bgr, x_lines, y_lines


def remove_cell_border_noise(cell_bgr):
    """
    Remove obvious crop-border leftovers around a cell.
    """
    if cv2 is None or np is None:
        return cell_bgr

    cell = cell_bgr.copy()
    h, w = cell.shape[:2]

    border = max(1, min(h, w) // 40)

    # Paint very edge pixels white, in case grid borders were included.
    cell[:border, :] = 255
    cell[h - border:, :] = 255
    cell[:, :border] = 255
    cell[:, w - border:] = 255

    return cell


def preprocess_cell_for_ocr(cell_bgr, kind: str = "text"):
    """
    kind:
      text
      percent
      number
    """
    if cv2 is None or np is None:
        return None

    cell = remove_cell_border_noise(cell_bgr)

    gray = cv2.cvtColor(cell, cv2.COLOR_BGR2GRAY)

    h, w = gray.shape[:2]

    # Add white padding around cell.
    pad_x = max(10, int(w * 0.08))
    pad_y = max(8, int(h * 0.20))
    gray = cv2.copyMakeBorder(
        gray,
        pad_y,
        pad_y,
        pad_x,
        pad_x,
        cv2.BORDER_CONSTANT,
        value=255,
    )

    # Enlarge for OCR.
    scale = 4 if kind in {"percent", "number"} else 3
    gray = cv2.resize(
        gray,
        None,
        fx=scale,
        fy=scale,
        interpolation=cv2.INTER_CUBIC,
    )

    gray = cv2.GaussianBlur(gray, (3, 3), 0)

    # Threshold.
    _, th = cv2.threshold(
        gray,
        0,
        255,
        cv2.THRESH_BINARY + cv2.THRESH_OTSU,
    )

    return th


def get_whitelist_for_cell_kind(kind: str) -> str:
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


def score_cell_ocr(text: str, kind: str) -> int:
    """
    Higher score = better OCR result.
    """
    text = normalize_spaces(text)
    if not text:
        return -999

    score = 0

    if kind == "number":
        if re.search(r"\d", text):
            score += 50
        if re.fullmatch(r"[0-9.,]+", text):
            score += 50
        if re.fullmatch(r"\d+(?:[.,]\d+)?", text):
            score += 100
        score -= len(re.sub(r"[0-9.,]", "", text)) * 10
        return score

    if kind == "percent":
        if re.search(r"\d", text):
            score += 50
        if "%" in text:
            score += 30
        if re.search(r"\d+(?:[.,]\d+)?\s*%?", text):
            score += 80
        score -= len(re.sub(r"[0-9.,% ]", "", text)) * 10
        return score

    # text / ingredient
    letters = len(re.findall(r"[A-Za-z가-힣]", text))
    digits = len(re.findall(r"\d", text))

    score += letters * 8
    score += min(digits, 4) * 2

    if len(text) >= 2:
        score += 10

    if re.search(r"[|_=~{}\[\]]", text):
        score -= 30

    if text.lower() in {"a", "i", "l", "1"}:
        score -= 80

    return score


def tesseract_cell_ocr(cell_img, kind: str = "text") -> str:
    """
    Try multiple PSM modes and keep the best result.
    """
    ensure_tesseract_ready()

    if cell_img is None:
        return ""

    whitelist = get_whitelist_for_cell_kind(kind)

    psm_modes = [7, 8, 13, 6]

    best_text = ""
    best_score = -999999

    for psm in psm_modes:
        config = (
            f"--oem 3 --psm {psm} "
            f"-c tessedit_char_whitelist={whitelist} "
            "-c preserve_interword_spaces=1"
        )

        try:
            text = pytesseract.image_to_string(
                cell_img,
                lang="eng",
                config=config,
            )
        except Exception:
            continue

        text = clean_ocr_text(text)
        text = text.replace("\n", " ")
        text = normalize_spaces(text)

        score = score_cell_ocr(text, kind)

        if score > best_score:
            best_score = score
            best_text = text

    return best_text.strip()


def ocr_grid_cell(cell_bgr, kind: str = "text") -> str:
    processed = preprocess_cell_for_ocr(cell_bgr, kind=kind)
    text = tesseract_cell_ocr(processed, kind=kind)
    return normalize_grid_cell_text(text)


def normalize_grid_cell_text(text: str) -> str:
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

    text = text.replace(",", ".")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def clean_numeric_cell(text: str) -> str:
    """
    OCR corrections for cells that should be numeric.
    """
    text = normalize_grid_cell_text(text)

    replacements = {
        "O": "0",
        "o": "0",
        "I": "1",
        "l": "1",
        "S": "5",
        "s": "5",
    }

    cleaned = ""
    for ch in text:
        cleaned += replacements.get(ch, ch)

    cleaned = cleaned.replace(" ", "")
    cleaned = cleaned.replace(",", ".")

    return cleaned


def extract_number_from_cell(text: str) -> str:
    text = clean_numeric_cell(text)
    m = re.search(r"\d+(?:\.\d+)?", text)
    return m.group(0) if m else ""


def extract_percent_from_cell(text: str) -> str:
    """
    Percentage column:
      "10%" -> "10"
      "10"  -> "10"
      "3.33%" -> "3.33"
    """
    text = clean_numeric_cell(text).replace("%", "")

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


def clean_ingredient_cell(text: str) -> str:
    text = normalize_grid_cell_text(text)

    # Remove obvious OCR leftovers.
    text = text.strip(" -–—,;:|")

    # Split accidental camelcase:
    # AmbroxSuper -> Ambrox Super
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
    }

    compact = text.replace(" ", "")
    if compact in compact_fixes:
        text = compact_fixes[compact]

    text = normalize_spaces(text)

    return text


def is_grid_header_row(cells: List[str]) -> bool:
    joined = " ".join(cells).lower()
    joined = re.sub(r"[^a-z0-9% ]+", " ", joined)
    joined = " ".join(joined.split())

    if not joined:
        return True

    header_words = [
        "ingredient",
        "gredient",
        "percentage",
        "percent",
        "amount",
        "material",
        "weight",
        "part",
    ]

    return any(word in joined for word in header_words)


def is_bad_grid_ingredient(text: str) -> bool:
    low = text.lower().strip()

    if not low:
        return True

    if low in {
        "a",
        "i",
        "l",
        "1",
        "ingredient",
        "percentage",
        "amount",
        "total",
    }:
        return True

    if not re.search(r"[A-Za-z가-힣]", text):
        return True

    if len(text) <= 1:
        return True

    return False


def crop_cell(img_bgr, x1: int, x2: int, y1: int, y2: int):
    h, w = img_bgr.shape[:2]

    cell_w = x2 - x1
    cell_h = y2 - y1

    if cell_w < 15 or cell_h < 8:
        return None

    # Crop inside borders.
    pad_x = max(3, int(cell_w * 0.025))
    pad_y = max(2, int(cell_h * 0.12))

    cx1 = max(0, x1 + pad_x)
    cx2 = min(w, x2 - pad_x)
    cy1 = max(0, y1 + pad_y)
    cy2 = min(h, y2 - pad_y)

    if cx2 <= cx1 or cy2 <= cy1:
        return None

    return img_bgr[cy1:cy2, cx1:cx2]


def choose_main_three_columns(x_lines: List[int]) -> List[Tuple[int, int]]:
    """
    Returns first three column spans from detected grid.
    In these formula images, columns are:
      Ingredient | Percentage | Amount
    """
    spans = []

    for i in range(len(x_lines) - 1):
        x1, x2 = x_lines[i], x_lines[i + 1]
        if x2 - x1 >= 20:
            spans.append((x1, x2))

    if len(spans) < 3:
        return []

    return spans[:3]


def run_grid_table_ocr_on_image(image: Image.Image) -> str:
    """
    Handles Excel-like bordered grid tables.

    Input table:
      Ingredient | Percentage | Amount
      Calone     | 10%        | 10
      Geosmin    | 1%         | 3.5
      Hedione    |            | 400

    Output:
      Calone 10% 10
      Geosmin 1% 3.5
      Hedione 400
    """
    if cv2 is None or np is None:
        return ""

    ensure_tesseract_ready()

    img_bgr, x_lines, y_lines = detect_grid_table(image)

    if img_bgr is None:
        return ""

    if len(x_lines) < 4 or len(y_lines) < 3:
        return ""

    h, w = img_bgr.shape[:2]

    # Prevent false positives.
    if len(x_lines) > 25 or len(y_lines) > 250:
        return ""

    col_spans = choose_main_three_columns(x_lines)
    if len(col_spans) < 3:
        return ""

    normalized_lines: List[str] = []

    for r in range(len(y_lines) - 1):
        y1, y2 = y_lines[r], y_lines[r + 1]

        if y2 - y1 < 12:
            continue

        cells = []

        for c, (x1, x2) in enumerate(col_spans):
            cell = crop_cell(img_bgr, x1, x2, y1, y2)

            if cell is None:
                cells.append("")
                continue

            if c == 0:
                kind = "text"
            elif c == 1:
                kind = "percent"
            else:
                kind = "number"

            text = ocr_grid_cell(cell, kind=kind)
            cells.append(text)

        if not any(cells):
            continue

        if is_grid_header_row(cells):
            continue

        ingredient = clean_ingredient_cell(cells[0])
        percentage_cell = cells[1]
        amount_cell = cells[2]

        if is_bad_grid_ingredient(ingredient):
            continue

        amount = extract_number_from_cell(amount_cell)
        if not amount:
            continue

        dilution = extract_percent_from_cell(percentage_cell)

        if dilution:
            normalized_lines.append(f"{ingredient} {dilution}% {amount}")
        else:
            normalized_lines.append(f"{ingredient} {amount}")

    useful_lines = [
        line for line in normalized_lines
        if re.search(r"[A-Za-z가-힣]", line) and re.search(r"\d", line)
    ]

    if len(useful_lines) < 3:
        return ""

    return "\n".join(useful_lines).strip()


# ------------------------------------------------------------
# Word-position OCR
# Handles non-grid tables:
# Formula entry | Weight (g) | % Rel | % Abs
# ------------------------------------------------------------

def get_ocr_words(image: Image.Image, psm: int = 6) -> List[OCRWord]:
    ensure_tesseract_ready()

    processed = preprocess_image_for_ocr(image)

    whitelist = (
        "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
        "abcdefghijklmnopqrstuvwxyz"
        "0123456789"
        " .,%()-_'\"®™/&:+"
    )

    config = (
        f"--oem 3 --psm {psm} "
        f"-c tessedit_char_whitelist={whitelist} "
        "-c preserve_interword_spaces=1"
    )

    data = pytesseract.image_to_data(
        processed,
        lang="eng",
        config=config,
        output_type=pytesseract.Output.DICT,
    )

    words: List[OCRWord] = []

    n = len(data.get("text", []))

    for i in range(n):
        raw_text = str(data["text"][i] or "").strip()
        text = clean_ocr_token(raw_text)

        if not text:
            continue

        try:
            conf = float(data["conf"][i])
        except Exception:
            conf = -1

        if conf < -1:
            continue

        words.append(
            OCRWord(
                text=text,
                left=int(data["left"][i]),
                top=int(data["top"][i]),
                width=int(data["width"][i]),
                height=int(data["height"][i]),
                conf=conf,
            )
        )

    return words


def group_words_into_rows(words: List[OCRWord]) -> List[List[OCRWord]]:
    if not words:
        return []

    words = sorted(words, key=lambda w: (w.center_y, w.left))

    heights = sorted([w.height for w in words if w.height > 0])
    median_h = heights[len(heights) // 2] if heights else 20
    y_threshold = max(10, median_h * 0.75)

    rows: List[List[OCRWord]] = []

    for word in words:
        placed = False

        for row in rows:
            row_y = sum(w.center_y for w in row) / len(row)

            if abs(word.center_y - row_y) <= y_threshold:
                row.append(word)
                placed = True
                break

        if not placed:
            rows.append([word])

    for row in rows:
        row.sort(key=lambda w: w.left)

    rows.sort(key=lambda r: sum(w.center_y for w in r) / len(r))

    return rows


def row_text(row: List[OCRWord]) -> str:
    return " ".join(w.text for w in sorted(row, key=lambda w: w.left))


def is_number_token(token: str) -> bool:
    token = token.strip()
    token = token.replace(",", ".")
    return bool(re.fullmatch(r"\d+(?:\.\d+)?", token))


def normalize_number_token(token: str) -> str:
    token = token.strip().replace(",", ".")
    return token


def parse_numeric_tokens_from_right(row: List[OCRWord]) -> Tuple[List[OCRWord], List[str]]:
    """
    Split row into left material words and trailing numeric columns.

    Example:
      Material (100%) 1.000 1.07 1.00

    Returns:
      left_words = Material (100%)
      numeric_tokens = [1.000, 1.07, 1.00]
    """
    sorted_words = sorted(row, key=lambda w: w.left)

    numeric_tokens: List[str] = []
    cut_index = len(sorted_words)

    for i in range(len(sorted_words) - 1, -1, -1):
        token = sorted_words[i].text.strip()

        if token in {"-", "—", "–"}:
            numeric_tokens.insert(0, token)
            cut_index = i
            continue

        if is_number_token(token):
            numeric_tokens.insert(0, normalize_number_token(token))
            cut_index = i
            continue

        break

    left_words = sorted_words[:cut_index]

    return left_words, numeric_tokens


def fix_material_spacing(text: str) -> str:
    text = text.strip()

    text = re.sub(r"([A-Za-z])(\d+%)", r"\1 \2", text)
    text = re.sub(r"([A-Za-z])\((\d+(?:\.\d+)?%)\)", r"\1 (\2)", text)

    # Split lower-to-upper transitions:
    # AurantiolUltra -> Aurantiol Ultra
    text = re.sub(r"([a-z])([A-Z])", r"\1 \2", text)

    text = " ".join(text.split())

    return text


def extract_material_and_dilution(left_text: str) -> Tuple[str, str]:
    """
    Accepts:
      Aurantiol Ultra (100%)
      Calone 10%
      Galaxolide50
      VioletLeafAbsolute1

    Returns:
      material, dilution
    """
    text = fix_material_spacing(left_text)

    # Parenthesized dilution.
    m = re.search(r"\((\d+(?:\.\d+)?)\s*%\)", text)
    if m:
        dilution = m.group(1)
        material = (text[: m.start()] + text[m.end() :]).strip()
        material = material.strip(" -–—,;:")
        material = " ".join(material.split())
        return material, dilution

    # Normal percentage.
    m = re.search(r"(?<!\w)(\d+(?:\.\d+)?)\s*%", text)
    if m:
        dilution = m.group(1)
        material = (text[: m.start()] + text[m.end() :]).strip()
        material = material.strip(" -–—,;:")
        material = " ".join(material.split())
        return material, dilution

    # OCR may remove % and parentheses:
    # Galaxolide50, Florol100, VioletLeafAbsolute1
    m = re.search(r"^(?P<name>.+?)(?P<dil>\d{1,3})$", text)
    if m:
        possible_dil = m.group("dil")
        name = m.group("name").strip()

        try:
            d = float(possible_dil)
            if 0 < d <= 100 and len(name) >= 3:
                return fix_material_spacing(name), possible_dil
        except Exception:
            pass

    return text, ""


def looks_like_header_or_total(text: str) -> bool:
    low = text.lower()
    low = re.sub(r"[^a-z0-9%(). ]+", " ", low)
    low = " ".join(low.split())

    if not low:
        return True

    if "formula entry" in low or "ingredient" in low:
        return True

    if "weight" in low and ("rel" in low or "abs" in low):
        return True

    if "gredient" in low and "amount" in low:
        return True

    if low.startswith("total"):
        return True

    return False


def looks_like_solvent_row(text: str) -> bool:
    low = text.lower()
    return "perfumer" in low and "alcohol" in low


def table_rows_to_formula_lines(rows: List[List[OCRWord]]) -> List[str]:
    """
    Reconstruct formula lines from OCR word positions.

    Output:
      Material Dilution% Part
    """
    formula_lines: List[str] = []

    for row in rows:
        txt = row_text(row)

        if looks_like_header_or_total(txt):
            continue

        if looks_like_solvent_row(txt):
            continue

        left_words, numeric_tokens = parse_numeric_tokens_from_right(row)

        if not left_words or not numeric_tokens:
            continue

        # In tables like:
        # Material  Weight  %Rel  %Abs
        # numeric_tokens = [weight, rel, abs]
        # We want the first numeric token after material as part/weight.
        part = numeric_tokens[0]

        if part in {"-", "—", "–"}:
            continue

        left = " ".join(w.text for w in left_words)
        material, dilution = extract_material_and_dilution(left)

        if not material or not re.search(r"[A-Za-z가-힣]", material):
            continue

        if looks_like_header_or_total(material):
            continue

        if dilution:
            formula_lines.append(f"{material} {dilution}% {part}")
        else:
            formula_lines.append(f"{material} {part}")

    return formula_lines


def score_table_text(text: str) -> int:
    lines = [line for line in text.splitlines() if line.strip()]

    score = len(lines) * 20

    for line in lines:
        if re.search(r"[A-Za-z가-힣]", line) and re.search(r"\d", line):
            score += 15
        if re.search(r"\d+(?:\.\d+)?%", line):
            score += 5

    return score


def run_table_ocr_on_image(image: Image.Image) -> str:
    """
    Table-aware OCR using word positions.
    Tries multiple PSM modes and keeps best result.
    """
    best_text = ""
    best_score = -999

    for psm in [6, 4, 11]:
        try:
            words = get_ocr_words(image, psm=psm)
            rows = group_words_into_rows(words)
            lines = table_rows_to_formula_lines(rows)
            text = "\n".join(lines).strip()
            score = score_table_text(text)

            if score > best_score:
                best_score = score
                best_text = text

        except Exception:
            continue

    return best_text.strip()


# ------------------------------------------------------------
# Plain OCR fallback
# ------------------------------------------------------------

def run_plain_ocr_on_image(image: Image.Image) -> str:
    ensure_tesseract_ready()

    processed = preprocess_image_for_ocr(image)

    whitelist = (
        "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
        "abcdefghijklmnopqrstuvwxyz"
        "0123456789"
        " .,%()-_'\"®™/&:+"
    )

    best_text = ""
    best_score = -999

    for psm in [6, 4, 11]:
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
        score = score_table_text(text)

        if score > best_score:
            best_score = score
            best_text = text

    return best_text.strip()


# ------------------------------------------------------------
# Public API used by app.py
# ------------------------------------------------------------

def run_ocr_on_image(image: Image.Image) -> str:
    """
    OCR priority:
      1. Bordered grid table OCR
         Good for Ingredient | Percentage | Amount tables.
      2. Word-position table OCR
         Good for Formula entry | Weight | % Rel | % Abs tables.
      3. Plain OCR fallback
    """

    # 1. Excel-like bordered grid table OCR.
    try:
        grid_text = run_grid_table_ocr_on_image(image)
        grid_lines = [line for line in grid_text.splitlines() if line.strip()]

        if len(grid_lines) >= 3:
            print("=== OCR MODE: GRID TABLE ===")
            print(grid_text)
            print("============================")
            return grid_text

    except Exception as e:
        print(f"[OCR] grid table OCR failed: {e}")

    # 2. Word-position table OCR.
    try:
        table_text = run_table_ocr_on_image(image)
        table_lines = [line for line in table_text.splitlines() if line.strip()]

        if len(table_lines) >= 3:
            print("=== OCR MODE: WORD TABLE ===")
            print(table_text)
            print("============================")
            return table_text

    except Exception as e:
        print(f"[OCR] word table OCR failed: {e}")

    # 3. Plain fallback.
    try:
        plain_text = run_plain_ocr_on_image(image)
        print("=== OCR MODE: PLAIN ===")
        print(plain_text)
        print("=======================")
        return plain_text

    except Exception as e:
        print(f"[OCR] plain OCR failed: {e}")
        return ""


def ocr_from_file(file_path: str) -> str:
    image = Image.open(file_path)
    return run_ocr_on_image(image)


def ocr_from_clipboard() -> str:
    image = ImageGrab.grabclipboard()

    if image is None:
        raise RuntimeError("No image found in clipboard.")

    if isinstance(image, list):
        if not image:
            raise RuntimeError("No image found in clipboard.")
        image = Image.open(image[0])

    if not isinstance(image, Image.Image):
        raise RuntimeError("Clipboard content is not an image.")

    return run_ocr_on_image(image)