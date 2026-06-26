import re


# ------------------------------------------------------------
# Formula line patterns
# ------------------------------------------------------------

# 390 - Bergamot
PART_FIRST_PATTERN = re.compile(
    r"^\s*(?P<part>\d+(?:\.\d+)?)\s*[-–—]\s*(?P<name>.+?)\s*$"
)

# Bergamot    390
TAB_PATTERN = re.compile(
    r"^\s*(?P<name>.+?)\t+(?P<part>\d+(?:\.\d+)?)\s*$"
)

# Bergamot 390
TRAILING_PART_PATTERN = re.compile(
    r"^\s*(?P<name>.+?)\s+(?P<part>\d+(?:\.\d+)?)\s*$"
)

# Hedione (100%) 33.000 35.16 33.00
TABLE_WITH_DILUTION_PATTERN = re.compile(
    r"^\s*(?P<name>.+?)\s*\((?P<dilution>\d+(?:\.\d+)?)\s*%\)\s+"
    r"(?P<part>\d+(?:\.\d+)?)"
    r"(?:\s+\d+(?:\.\d+)?){0,4}\s*$"
)

# Preferred OCR normalized output:
# Calone 10% 10
# Geosmin 1% 3.5
# Hedione 400
# ISO E Super 200
# Prismantol 3.33% 35
OCR_INGREDIENT_PERCENT_AMOUNT_PATTERN = re.compile(
    r"^\s*(?P<name>[A-Za-z][A-Za-z0-9\s,\-_'\"®™()/&.+]+?)\s+"
    r"(?:(?P<dilution>\d+(?:\.\d+)?)\s*%\s+)?"
    r"(?P<part>\d+(?:\.\d+)?)\s*$"
)

# Catch dilution inside material name:
# Cumin 10%
# Oud Assafi 10%
DILUTION_IN_NAME_PATTERN = re.compile(
    r"(?<!\w)(?P<dilution>\d+(?:\.\d+)?)\s*%"
)


SKIP_KEYWORDS = [
    "this formula is provided",
    "we provide no warranties",
    "you access and use",
    "any implied conditions",
    "to the extent",
    "you indemnify",
    "direct or indirect",
    "formula is limited",
    "warranties are excluded",
    "loss we suffer",
    "©",
    "copyright",
    "contd",
    "continued",
    "a woody fragrance",
    "nzd",
    "formula entry",
    "ingredient percentage amount",
    "ingredient percent amount",
    "ingredient amount",
    "weight (g)",
    "% rel",
    "% abs",

    # Broken OCR header fragments from bordered tables
    "gredientpercentageamount",
    "gredient percentage amount",
]


HEADER_LIKE_LINES = {
    "ingredient",
    "percentage",
    "amount",
    "ingredient percentage amount",
    "ingredient percent amount",
    "ingredient amount",
    "gredientpercentageamount",
    "gredient percentage amount",
    "formula entry",
    "formula entry weight g rel abs",
    "material",
    "part",
    "dilution",
    "weight",
    "total",
}


def clean_line(line: str) -> str:
    line = line.strip()
    line = line.replace("\u2026", "...")
    line = line.replace("\r", "")
    line = line.replace("|", " ")
    line = line.replace("—", "-")
    line = line.replace("–", "-")
    line = line.replace("“", '"')
    line = line.replace("”", '"')
    line = line.replace("‘", "'")
    line = line.replace("’", "'")
    line = line.replace("\u00a0", " ")

    # OCR corrections.
    line = line.replace("％", "%")
    line = re.sub(r"\s+", " ", line)

    return line.strip()


def normalized_for_skip(line: str) -> str:
    low = line.lower().strip()
    low = re.sub(r"[^a-z0-9%(). ]+", " ", low)
    low = " ".join(low.split())
    return low


def should_skip_line(line: str) -> tuple[bool, str]:
    low = normalized_for_skip(line)

    if not low:
        return True, "blank"

    if low in HEADER_LIKE_LINES:
        return True, "header / total"

    if low.startswith("total "):
        return True, "total row"

    if "perfumer" in low and "alcohol" in low:
        return True, "solvent row"

    if low in {"...", "... contd", "contd", "continued"}:
        return True, "continuation marker"

    for keyword in SKIP_KEYWORDS:
        if keyword in low:
            return True, "disclaimer / non-formula text"

    if len(line) > 120 and not re.search(r"\d+(?:\.\d+)?\s*$", line):
        return True, "long non-formula text"

    return False, ""


def cleanup_material_name(name: str) -> str:
    name = name.strip()
    name = name.replace("％", "%")
    name = re.sub(r"\s{2,}", " ", name)
    name = name.strip(" -–—,;:")

    # OCR sometimes reads header fragments into names.
    name = re.sub(r"^(ingredient|formula entry)\s+", "", name, flags=re.I)

    # Split some camelcase OCR accidents:
    # VioletLeafAbsolute -> Violet Leaf Absolute
    # AurantiolUltra -> Aurantiol Ultra
    name = re.sub(r"([a-z])([A-Z])", r"\1 \2", name)

    # Known compact OCR names.
    known_compact = {
        "ISOESuper": "ISO E Super",
        "IsoESuper": "Iso E Super",
        "LemonOil": "Lemon Oil",
        "RoseEssence": "Rose Essence",
        "EthylLinalol": "Ethyl linalol",
        "EthylLinalool": "Ethyl linalool",
        "EthyleneBrassylate": "Ethylene brassylate",
        "VioletLeafAbsolute": "Violet Leaf Absolute",
        "Dihydromyrcenol": "Dihydromyrcenol",
        "AmbroxSuper": "Ambrox Super",
        "SechuanPepper": "Sechuan Pepper",
        "Jasmineacc": "Jasmine acc",
    }

    compact = name.replace(" ", "")
    if compact in known_compact:
        name = known_compact[compact]

    return name.strip()


def extract_dilution_from_name(name: str) -> tuple[str, str]:
    """
    Examples:
      'Cumin 10%' -> ('Cumin', '10')
      'Oud Assafi 10%' -> ('Oud Assafi', '10')
      'Galbanum Oil 10%' -> ('Galbanum Oil', '10')
      'Citronellol 98' -> ('Citronellol 98', '')
    """
    match = DILUTION_IN_NAME_PATTERN.search(name)

    if not match:
        return cleanup_material_name(name), ""

    dilution = match.group("dilution")
    cleaned = name[:match.start()] + name[match.end():]
    cleaned = cleanup_material_name(cleaned)

    return cleaned, dilution


def valid_material_name(material: str) -> bool:
    if not material:
        return False

    if not re.search(r"[A-Za-z가-힣]", material):
        return False

    low = normalized_for_skip(material)

    if low in HEADER_LIKE_LINES:
        return False

    if len(material) <= 1:
        return False

    return True


def validate_part(part: str) -> bool:
    try:
        value = float(part)
        return value > 0
    except Exception:
        return False


def make_result(
    include: bool,
    material: str,
    part: str,
    dilution: str,
    original: str,
    reason: str,
    raw_part: str = "",
) -> dict:
    return {
        "include": include,
        "material": material,
        "part": part,
        "raw_part": raw_part or part,
        "dilution": dilution,
        "original": original,
        "reason": reason,
    }


def make_diluted_stock_result(
    material: str,
    raw_part: str,
    dilution: str,
    original_line: str,
    reason: str,
) -> dict:
    """
    Parser stage preserves what was read from the formula.

    Example:
      Calone 10% 10

    Parse Preview should show:
      material = Calone
      part = 10
      dilution = 10

    Conversion to pure-material-equivalent part must happen later,
    when importing into the main GUI table.
    """
    return make_result(
        True,
        material,
        raw_part,
        dilution,
        original_line,
        reason,
        raw_part=raw_part,
    )


def try_parse_formula_line(original_line: str) -> dict:
    line = clean_line(original_line)

    skip, reason = should_skip_line(line)
    if skip:
        return make_result(False, "", "", "", original_line, reason)

    # --------------------------------------------------------
    # 1. OCR table with explicit dilution in parentheses:
    # Hedione (100%) 33.000 35.16 33.00
    #
    # Parser only stores the raw part.
    # Conversion happens during import to main GUI.
    # --------------------------------------------------------
    match = TABLE_WITH_DILUTION_PATTERN.match(line)
    if match:
        material = cleanup_material_name(match.group("name"))
        dilution = match.group("dilution").strip()
        raw_part = match.group("part").strip()

        if not valid_material_name(material):
            return make_result(False, material, raw_part, dilution, original_line, "invalid material name")

        if not validate_part(raw_part):
            return make_result(False, material, raw_part, dilution, original_line, "invalid part number")

        return make_diluted_stock_result(
            material,
            raw_part,
            dilution,
            original_line,
            "OCR table row",
        )

    # --------------------------------------------------------
    # 2. OCR Ingredient / Percentage / Amount style:
    # Calone 10% 10
    # Hedione 400
    # Jasmine acc (CP) 55
    #
    # Parser only stores the raw part.
    # Conversion happens during import to main GUI.
    # --------------------------------------------------------
    match = OCR_INGREDIENT_PERCENT_AMOUNT_PATTERN.match(line)
    if match:
        material = cleanup_material_name(match.group("name"))
        dilution = match.group("dilution") or ""
        raw_part = match.group("part").strip()

        material, name_dilution = extract_dilution_from_name(material)
        if not dilution and name_dilution:
            dilution = name_dilution

        if not valid_material_name(material):
            return make_result(False, material, raw_part, dilution, original_line, "invalid material name")

        if not validate_part(raw_part):
            return make_result(False, material, raw_part, dilution, original_line, "invalid part number")

        if dilution:
            return make_diluted_stock_result(
                material,
                raw_part,
                dilution,
                original_line,
                "OCR ingredient table row",
            )

        return make_result(
            True,
            material,
            raw_part,
            dilution,
            original_line,
            "OCR ingredient table row",
            raw_part=raw_part,
        )

    # --------------------------------------------------------
    # 3. Part first:
    # 390 - Bergamot
    # --------------------------------------------------------
    match = PART_FIRST_PATTERN.match(line)
    parser = "part - material"

    # --------------------------------------------------------
    # 4. Tab separated:
    # Bergamot    390
    # --------------------------------------------------------
    if not match:
        match = TAB_PATTERN.match(line)
        parser = "tab separated"

    # --------------------------------------------------------
    # 5. Trailing part:
    # Bergamot FCF 125
    # Oud Assafi 10% 20
    #
    # Parser only stores the raw part.
    # Conversion happens during import to main GUI.
    # --------------------------------------------------------
    if not match:
        match = TRAILING_PART_PATTERN.match(line)
        parser = "trailing part"

    if not match:
        return make_result(False, "", "", "", original_line, "no formula pattern found")

    material = match.group("name").strip()
    raw_part = match.group("part").strip()

    material, dilution = extract_dilution_from_name(material)

    if not valid_material_name(material):
        return make_result(False, material, raw_part, dilution, original_line, "invalid material name")

    if not validate_part(raw_part):
        return make_result(False, material, raw_part, dilution, original_line, "invalid part number")

    if dilution:
        return make_diluted_stock_result(
            material,
            raw_part,
            dilution,
            original_line,
            parser,
        )

    return make_result(
        True,
        material,
        raw_part,
        dilution,
        original_line,
        parser,
        raw_part=raw_part,
    )


def parse_formula_text_advanced(text: str) -> list[dict]:
    parsed = []

    for raw_line in text.splitlines():
        raw_line = raw_line.rstrip()

        if not raw_line.strip():
            continue

        parsed.append(try_parse_formula_line(raw_line))

    return parsed