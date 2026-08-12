from __future__ import annotations
from dataclasses import dataclass, field
import re

CAS_RE = re.compile(r'\b\d{2,7}-\d{2}-\d\b')

@dataclass
class Exposure:
    cas: str
    name: str
    finished_product_pct: float
    sources: list[str] = field(default_factory=list)

@dataclass
class ComplianceResult:
    cas: str
    name: str
    actual_pct: float
    max_pct: float | None
    raw_limit: str
    status: str
    sources: list[str]
    standard_type: str

    @property
    def use_of_limit_pct(self) -> float | None:
        if self.max_pct is None:return None
        if self.max_pct <= 0:return float('inf') if self.actual_pct > 0 else 0.0
        return self.actual_pct / self.max_pct * 100.0

    def excess_mg(self, batch_g: float) -> float:
        if self.max_pct is None:return 0.0
        excess_pct=max(0.0,self.actual_pct-float(self.max_pct))
        return excess_pct/100.0*max(0.0,float(batch_g or 0))*1000.0


def split_cas(value: str | None) -> list[str]:
    if not value:
        return []
    return CAS_RE.findall(str(value))


def _resolved_material(db, material_row):
    # Formula parts are already active-material parts. Inventory concentration only changes how much
    # stock solution is weighed; it must not reduce IFRA exposure a second time. A predilution may
    # still point to a parent identity whose CAS should be used for compliance.
    if material_row['parent_material_id']:
        parent = db.material_by_id(int(material_row['parent_material_id']))
        if parent:
            return parent, 1.0
    return material_row, 1.0


def formula_exposures(db, formula_id: int):
    frows = db.query('SELECT * FROM formulas WHERE id=?', (formula_id,))
    if not frows:
        raise ValueError('Formula not found')
    formula = frows[0]
    items = db.query('''SELECT fi.parts, fi.material_name AS formula_material_name, m.* FROM formula_items fi
                        LEFT JOIN materials m ON m.id=fi.material_id
                        WHERE fi.formula_id=? AND COALESCE(fi.disabled,0)=0 ORDER BY fi.sort_order, fi.id''', (formula_id,))
    total = sum(max(0.0, float(x['parts'] or 0)) for x in items)
    if total <= 0:
        return formula, []
    fragrance_load = float(formula['fragrance_load_pct'] or 0) / 100.0
    exposures: dict[str, Exposure] = {}

    def add(cas, name, pct, source):
        cas = (cas or '').strip()
        if not cas or pct <= 0:
            return
        if cas not in exposures:
            exposures[cas] = Exposure(cas, name or cas, 0.0, [])
        exposures[cas].finished_product_pct += pct
        exposures[cas].sources.append(source)

    for item in items:
        # Unmatched formula rows are preserved for composition, so they stay in the denominator but
        # cannot be evaluated until the user links them to Inventory/CAS data.
        if item['id'] is None:
            continue
        item_formula_pct = float(item['parts'] or 0) / total * 100.0
        resolved, active_fraction = _resolved_material(db, item)
        active_finished_pct = item_formula_pct * fragrance_load * active_fraction
        material_cas = split_cas(resolved['cas'])
        # Direct ingredient exposure.
        for cas in material_cas:
            add(cas, resolved['name'], active_finished_pct, item['formula_material_name'] or item['name'])

        # Natural contributions: match any principal/other CAS in the official annex.
        matched_ncs = []
        for cas in material_cas:
            matched_ncs += db.query('''SELECT * FROM ncs_contributions
                                       WHERE principal_cas LIKE ? OR other_cas LIKE ?''', (f'%{cas}%', f'%{cas}%'))
        seen = set()
        for ncs in matched_ncs:
            key = (ncs['ncs_name'], ncs['constituent_cas'], ncs['concentration_pct'])
            if key in seen:
                continue
            seen.add(key)
            constituent_pct = active_finished_pct * float(ncs['concentration_pct']) / 100.0
            add(str(ncs['constituent_cas']), str(ncs['constituent_name']), constituent_pct,
                f"{item['formula_material_name'] or item['name']} → {ncs['constituent_name']} ({ncs['concentration_pct']}%)")
    return formula, list(exposures.values())


def check_formula(db, formula_id: int, category: str | None = None) -> list[ComplianceResult]:
    formula, exposures = formula_exposures(db, formula_id)
    category = category or str(formula['ifra_category'])
    results = []
    for exp in exposures:
        rows = db.query('''SELECT s.*, l.max_pct, l.raw_value FROM ifra_standards s
                           JOIN ifra_limits l ON l.standard_id=s.id
                           WHERE l.category=?''', (category,))
        standard = None
        for row in rows:
            if exp.cas in split_cas(row['cas_numbers']):
                standard = row
                break
        if not standard:
            continue
        max_pct = standard['max_pct']
        raw = standard['raw_value'] or ''
        stype = standard['standard_type'] or ''
        if 'PROHIBITION' in stype and (max_pct is None or max_pct <= 0):
            status = 'PROHIBITED' if exp.finished_product_pct > 0 else 'OK'
        elif max_pct is None:
            status = 'REVIEW'
        else:
            status = 'OK' if exp.finished_product_pct <= float(max_pct) + 1e-12 else 'OVER'
        results.append(ComplianceResult(exp.cas, standard['name'], exp.finished_product_pct,
                                        None if max_pct is None else float(max_pct), raw, status,
                                        exp.sources, stype))
    def rank(x):
        ratio=x.use_of_limit_pct
        if ratio is None:return -1.0
        return ratio
    return sorted(results,key=rank,reverse=True)
