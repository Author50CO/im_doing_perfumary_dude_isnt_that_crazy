from __future__ import annotations

"""Importer for XML formulas created by Author50CO/im_doing_perfumary_dude_isnt_that_crazy.

The XML shape mirrors perfume_tool/formula_storage.py in that repository:
<formula><metadata>...</metadata><inputs>...</inputs><rows><row>...</row></rows></formula>
or a <formulas> bundle containing multiple <formula> elements.
"""

from pathlib import Path
import xml.etree.ElementTree as ET

from perfume_studio.services.inventory_enrichment import normalize_material_name
from perfume_studio.services.transparency_lookup import resolve_local_identity


def _text(parent: ET.Element | None, tag: str, default: str = "") -> str:
    if parent is None:
        return default
    node = parent.find(tag)
    if node is None or node.text is None:
        return default
    return node.text.strip()


def _float(value, default=0.0) -> float:
    try:
        return float(str(value).strip().replace(',', '.'))
    except Exception:
        return float(default)


def _bool(value) -> bool:
    return str(value or '').strip().lower() in {'true', '1', 'yes', 'y'}


def element_to_formula(elem: ET.Element, file_path: str = '') -> dict:
    metadata = elem.find('metadata')
    inputs = elem.find('inputs')
    rows_node = elem.find('rows')
    formula = {
        'id': elem.get('id') or '',
        'name': _text(metadata, 'name', 'Imported Formula'),
        'created_date': _text(metadata, 'created_date', ''),
        'description': _text(metadata, 'description', ''),
        'saved_at': _text(metadata, 'saved_at', ''),
        'file_path': file_path,
        'inputs': {
            'target_weight': _text(inputs, 'target_weight', '100'),
            'default_dilution': _text(inputs, 'default_dilution', '100'),
            'desired_dilution': _text(inputs, 'desired_dilution', '20'),
            'maximum_dilution': _text(inputs, 'maximum_dilution', '100'),
        },
        'source_text': _text(elem, 'source_text', ''),
        'rows': [],
    }
    if rows_node is not None:
        for row_node in rows_node.findall('row'):
            part_text = _text(row_node, 'part', '0')
            part = _float(part_text, 0)
            row = {
                'material': _text(row_node, 'material', ''),
                'part': part,
                'manual_dilution': _text(row_node, 'manual_dilution', ''),
                'part_adjusted_by_dilution': _bool(_text(row_node, 'part_adjusted_by_dilution', 'false')),
                'parsed_part': _text(row_node, 'parsed_part', part_text),
                'parsed_dilution': _text(row_node, 'parsed_dilution', _text(row_node, 'manual_dilution', '')),
            }
            if row['material'] and row['part'] > 0:
                formula['rows'].append(row)
    return formula


def read_formulas_from_xml(file_path: str | Path) -> list[dict]:
    path = Path(file_path)
    tree = ET.parse(path)
    root = tree.getroot()
    if root.tag == 'formula':
        return [element_to_formula(root, str(path))]
    if root.tag == 'formulas':
        return [element_to_formula(x, str(path)) for x in root.findall('formula')]
    raise ValueError('Invalid formula XML file. Expected <formula> or <formulas>.')


def _unique_formula_name(db, preferred: str) -> str:
    base = (preferred or 'Imported Formula').strip() or 'Imported Formula'
    used = {r['name'] for r in db.query('SELECT name FROM formulas')}
    if base not in used:
        return base
    i = 2
    while f'{base} ({i})' in used:
        i += 1
    return f'{base} ({i})'


def _dilution_pct(row: dict) -> float:
    raw = row.get('manual_dilution') or row.get('parsed_dilution') or ''
    if not str(raw).strip():
        return 100.0
    value = _float(str(raw).replace('%', ''), 100)
    return value if 0 < value <= 100 else 100.0


def _find_material(db, name: str, pct: float):
    norm = normalize_material_name(name)
    candidates = []
    for m in db.list_materials():
        if normalize_material_name(m['name']) != norm:
            continue
        candidates.append(m)
    if not candidates:
        return None
    for m in candidates:
        try:
            if abs(float(m['concentration_pct'] or 100) - pct) < 0.001:
                return m
        except Exception:
            pass
    if abs(pct - 100.0) < 0.001:
        for m in candidates:
            if abs(float(m['concentration_pct'] or 100) - 100.0) < 0.001:
                return m
    return None


def _create_missing_material(db, name: str, pct: float) -> int:
    identity = resolve_local_identity(db, name)
    cas = identity['cas'] if identity else ''
    mtype = 'dilution' if pct < 99.999 else 'raw'
    return db.execute(
        '''INSERT INTO materials(name,cas,material_type,concentration_pct,unit_cost_per_g,currency,stock_g)
           VALUES(?,?,?,?,?,?,?)''',
        (name, cas, mtype, pct, 0.0, 'USD', 0.0)
    )


def import_formula_xml_to_db(db, file_path: str | Path, create_missing_materials: bool = False) -> dict:
    # Compatibility wrapper. Unified importer owns the storage invariant (sum(parts) == 1000).
    from perfume_studio.services.formula_io import import_formula_to_db
    return import_formula_to_db(db, file_path, create_missing_materials=create_missing_materials)
