import os
import re
import sys
import uuid
import shutil
import xml.etree.ElementTree as ET
from datetime import datetime


FORMULA_DIR_NAME = "formulas"


def app_base_dir() -> str:
    """
    For portable exe:
      base dir = folder containing PerfumeCalculator.exe

    For normal Python run:
      base dir = current working directory, usually folder containing run.py
    """
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)

    return os.path.abspath(".")


def formulas_dir() -> str:
    path = os.path.join(app_base_dir(), FORMULA_DIR_NAME)
    os.makedirs(path, exist_ok=True)
    return path


def sanitize_filename(name: str) -> str:
    name = str(name or "").strip()
    if not name:
        name = "Untitled Formula"

    name = re.sub(r'[<>:"/\\|?*]+', "_", name)
    name = re.sub(r"\s+", " ", name).strip()
    name = name[:90].strip()

    return name or "Untitled Formula"


def unique_xml_path(directory: str, preferred_name: str) -> str:
    safe = sanitize_filename(preferred_name)
    path = os.path.join(directory, f"{safe}.xml")

    if not os.path.exists(path):
        return path

    i = 2
    while True:
        path = os.path.join(directory, f"{safe} ({i}).xml")
        if not os.path.exists(path):
            return path
        i += 1


def indent_xml(elem, level: int = 0):
    """
    Pretty-print XML for easier sharing/editing.
    """
    i = "\n" + level * "  "

    if len(elem):
        if not elem.text or not elem.text.strip():
            elem.text = i + "  "

        for child in elem:
            indent_xml(child, level + 1)

        if not child.tail or not child.tail.strip():
            child.tail = i

    if level and (not elem.tail or not elem.tail.strip()):
        elem.tail = i


def safe_text(parent, tag: str, default: str = "") -> str:
    node = parent.find(tag)
    if node is None or node.text is None:
        return default
    return node.text


def bool_to_text(value) -> str:
    return "true" if bool(value) else "false"


def text_to_bool(value) -> bool:
    return str(value or "").strip().lower() in {"true", "1", "yes", "y"}


def formula_to_element(formula: dict) -> ET.Element:
    formula_id = str(formula.get("id") or uuid.uuid4())

    root = ET.Element("formula")
    root.set("id", formula_id)

    metadata = ET.SubElement(root, "metadata")

    ET.SubElement(metadata, "name").text = str(
        formula.get("name") or "Untitled Formula"
    )
    ET.SubElement(metadata, "created_date").text = str(
        formula.get("created_date") or datetime.now().strftime("%Y-%m-%d")
    )
    ET.SubElement(metadata, "description").text = str(
        formula.get("description") or ""
    )
    ET.SubElement(metadata, "saved_at").text = datetime.now().isoformat(timespec="seconds")

    inputs = formula.get("inputs") or {}
    inputs_node = ET.SubElement(root, "inputs")
    ET.SubElement(inputs_node, "target_weight").text = str(inputs.get("target_weight", "10"))
    ET.SubElement(inputs_node, "default_dilution").text = str(inputs.get("default_dilution", "20"))
    ET.SubElement(inputs_node, "desired_dilution").text = str(inputs.get("desired_dilution", "10"))
    ET.SubElement(inputs_node, "maximum_dilution").text = str(inputs.get("maximum_dilution", "30"))

    source_text = str(formula.get("source_text") or "")
    ET.SubElement(root, "source_text").text = source_text

    rows_node = ET.SubElement(root, "rows")

    for row in formula.get("rows", []):
        row_node = ET.SubElement(rows_node, "row")

        ET.SubElement(row_node, "material").text = str(row.get("material", ""))
        ET.SubElement(row_node, "part").text = str(row.get("part", ""))
        ET.SubElement(row_node, "manual_dilution").text = str(row.get("manual_dilution", ""))

        ET.SubElement(row_node, "part_adjusted_by_dilution").text = bool_to_text(
            row.get("part_adjusted_by_dilution", False)
        )

        ET.SubElement(row_node, "parsed_part").text = str(row.get("parsed_part", row.get("part", "")))
        ET.SubElement(row_node, "parsed_dilution").text = str(
            row.get("parsed_dilution", row.get("manual_dilution", ""))
        )

    return root


def element_to_formula(elem: ET.Element, file_path: str = "") -> dict:
    """
    Reads one <formula> element.
    """
    metadata = elem.find("metadata")
    inputs = elem.find("inputs")
    rows_node = elem.find("rows")

    if metadata is None:
        metadata = ET.Element("metadata")

    if inputs is None:
        inputs = ET.Element("inputs")

    formula = {
        "id": elem.get("id") or str(uuid.uuid4()),
        "name": safe_text(metadata, "name", "Untitled Formula"),
        "created_date": safe_text(metadata, "created_date", ""),
        "description": safe_text(metadata, "description", ""),
        "saved_at": safe_text(metadata, "saved_at", ""),
        "file_path": file_path,
        "inputs": {
            "target_weight": safe_text(inputs, "target_weight", "10"),
            "default_dilution": safe_text(inputs, "default_dilution", "20"),
            "desired_dilution": safe_text(inputs, "desired_dilution", "10"),
            "maximum_dilution": safe_text(inputs, "maximum_dilution", "30"),
        },
        "source_text": safe_text(elem, "source_text", ""),
        "rows": [],
    }

    if rows_node is not None:
        for row_node in rows_node.findall("row"):
            part_text = safe_text(row_node, "part", "0")

            try:
                part = float(part_text)
            except Exception:
                part = 0.0

            row = {
                "material": safe_text(row_node, "material", ""),
                "part": part,
                "manual_dilution": safe_text(row_node, "manual_dilution", ""),
                "part_adjusted_by_dilution": text_to_bool(
                    safe_text(row_node, "part_adjusted_by_dilution", "false")
                ),
                "parsed_part": safe_text(row_node, "parsed_part", part_text),
                "parsed_dilution": safe_text(
                    row_node,
                    "parsed_dilution",
                    safe_text(row_node, "manual_dilution", ""),
                ),
            }

            if row["material"] and row["part"] > 0:
                formula["rows"].append(row)

    return formula


def save_formula_file(formula: dict, overwrite_path: str = "") -> str:
    directory = formulas_dir()

    if overwrite_path:
        path = overwrite_path
    else:
        path = unique_xml_path(directory, formula.get("name", "Untitled Formula"))

    root = formula_to_element(formula)
    indent_xml(root)

    tree = ET.ElementTree(root)
    tree.write(path, encoding="utf-8", xml_declaration=True)

    return path


def load_formula_file(file_path: str) -> dict:
    tree = ET.parse(file_path)
    root = tree.getroot()

    if root.tag == "formula":
        return element_to_formula(root, file_path=file_path)

    if root.tag == "formulas":
        first = root.find("formula")
        if first is None:
            raise ValueError("No formula found in XML file.")
        return element_to_formula(first, file_path=file_path)

    raise ValueError("Invalid formula XML file.")


def read_formulas_from_xml(file_path: str) -> list[dict]:
    tree = ET.parse(file_path)
    root = tree.getroot()

    formulas = []

    if root.tag == "formula":
        formulas.append(element_to_formula(root, file_path=file_path))
        return formulas

    if root.tag == "formulas":
        for elem in root.findall("formula"):
            formulas.append(element_to_formula(elem, file_path=file_path))
        return formulas

    raise ValueError("Invalid formula XML file.")


def list_local_formulas() -> list[dict]:
    directory = formulas_dir()
    items = []

    for filename in sorted(os.listdir(directory)):
        if not filename.lower().endswith(".xml"):
            continue

        path = os.path.join(directory, filename)

        try:
            formula = load_formula_file(path)
            formula["file_path"] = path
            formula["filename"] = filename
            items.append(formula)
        except Exception:
            continue

    return items


def export_formulas_to_bundle(formulas: list[dict], output_file: str) -> str:
    root = ET.Element("formulas")
    root.set("exported_at", datetime.now().isoformat(timespec="seconds"))
    root.set("count", str(len(formulas)))

    for formula in formulas:
        root.append(formula_to_element(formula))

    indent_xml(root)

    tree = ET.ElementTree(root)
    tree.write(output_file, encoding="utf-8", xml_declaration=True)

    return output_file


def import_formulas_to_local(formulas: list[dict]) -> list[str]:
    saved_paths = []

    for formula in formulas:
        path = unique_xml_path(formulas_dir(), formula.get("name", "Imported Formula"))
        saved_paths.append(save_formula_file(formula, overwrite_path=path))

    return saved_paths