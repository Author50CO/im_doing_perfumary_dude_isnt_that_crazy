from __future__ import annotations
from dataclasses import dataclass
from typing import Iterable, Mapping


@dataclass
class FormulaLine:
    """One active-material formula line.

    ``parts`` is always ACTIVE parts-per-thousand. Inventory is an optional manufacturing link:
    a formula row may exist even when the material is not registered in Inventory yet.
    """
    material_id: int | None
    name: str
    parts: float
    concentration_pct: float | None = 100.0
    unit_cost_per_g: float = 0.0
    stock_g: float = 0.0
    inventory_available: bool = True
    manual_override_pct: float | None = None


@dataclass
class CalculatedLine:
    material_id: int | None
    name: str
    parts: float
    formula_pct: float
    inventory_concentration_pct: float | None
    used_concentration_pct: float | None
    weight_g: float | None
    active_weight_g: float
    cost: float | None
    stock_after_g: float | None
    forced_neat: bool = False
    neat_material_id: int | None = None
    has_neat_inventory: bool = False
    inventory_available: bool = True


@dataclass
class ManufacturingPlan:
    lines: list[CalculatedLine]
    batch_g: float
    fragrance_load_pct: float
    active_total_g: float
    weighed_material_total_g: float | None
    solvent_g: float | None
    current_strength_without_solvent_pct: float | None
    cost_total: float
    forced_neat_count: int
    missing_neat_names: list[str]
    missing_inventory_names: list[str]
    material_cost_total: float = 0.0
    solvent_cost: float = 0.0
    solvent_cost_basis_g: float = 0.0

    @property
    def too_diluted_before_adjustment(self) -> bool:
        return self.forced_neat_count > 0

    @property
    def complete(self) -> bool:
        return not self.missing_inventory_names


def _safe_conc(value: float | None) -> float:
    try:
        v = float(value)
    except Exception:
        v = 100.0
    if v <= 0:
        return 100.0
    return min(100.0, v)


def calculate_manufacturing_plan(
    lines: Iterable[FormulaLine],
    batch_g: float,
    fragrance_load_pct: float,
    neat_by_name: Mapping[str, FormulaLine] | None = None,
    solvent_unit_cost_per_g: float = 0.0,
) -> ManufacturingPlan:
    """Convert an active 1000-part formula into a practical weighing plan.

    Formula composition is independent of Inventory dilution. For a complete Inventory mapping,
    stock weight = active target / stock concentration. If the combined prediluted stocks would
    weigh more than the requested finished batch, the largest diluted formula rows are changed to
    a 100% manufacturing override one-by-one until the target strength becomes possible.

    A row may also carry a manual manufacturing override concentration (1-100%). This affects only
    the weighing plan, never the active formula parts. If an automatic 100% override is needed, the
    selected Inventory row's price/g is treated as the 100% raw-material price/g and is applied only to active grams.

    If any formula material is absent from Inventory, the row is preserved but the overall strength
    and solvent addition are intentionally left unresolved instead of pretending that material is neat.
    """
    lines = [x for x in lines if float(x.parts or 0) > 0 and str(x.name or '').strip()]
    batch_g = max(0.0, float(batch_g or 0))
    load = max(0.0, min(100.0, float(fragrance_load_pct or 0)))
    total_parts = sum(max(0.0, float(x.parts or 0)) for x in lines)
    active_total_g = batch_g * load / 100.0

    if total_parts <= 0 or batch_g <= 0:
        solvent = batch_g if batch_g > 0 else 0.0
        solvent_cost_basis_g = batch_g * max(0.0, 1.0 - load / 100.0)
        solvent_cost = round(solvent_cost_basis_g * max(0.0, float(solvent_unit_cost_per_g or 0)), 2)
        return ManufacturingPlan([], batch_g, load, active_total_g, 0.0, solvent,
                                 0.0, solvent_cost, 0, [], [], 0.0, solvent_cost, solvent_cost_basis_g)

    provisional = []
    missing_inventory: list[str] = []
    for order, line in enumerate(lines):
        parts = max(0.0, float(line.parts or 0))
        formula_pct = parts / total_parts * 100.0
        active_g = active_total_g * parts / total_parts
        available = bool(line.inventory_available and line.material_id is not None)
        if not available:
            missing_inventory.append(line.name)
            provisional.append({
                'order': order, 'line': line, 'parts': parts, 'formula_pct': formula_pct,
                'active_g': active_g, 'inv_conc': None, 'used_conc': None, 'weight_g': None,
                'forced': False, 'manual': None,
            })
            continue
        inv_conc = _safe_conc(line.concentration_pct)
        manual = None
        try:
            if line.manual_override_pct is not None and str(line.manual_override_pct).strip() != '':
                manual = min(100.0, max(0.000001, float(line.manual_override_pct)))
        except Exception:
            manual = None
        used_conc = manual if manual is not None else inv_conc
        stock_g = active_g / (used_conc / 100.0)
        provisional.append({
            'order': order, 'line': line, 'parts': parts, 'formula_pct': formula_pct,
            'active_g': active_g, 'inv_conc': inv_conc, 'used_conc': used_conc,
            'weight_g': stock_g, 'forced': False, 'manual': manual,
        })

    complete = not missing_inventory
    total_weight: float | None = None
    if complete:
        total_weight = sum(float(x['weight_g'] or 0) for x in provisional)
        if total_weight > batch_g + 1e-12:
            for x in sorted(provisional, key=lambda a: (-a['parts'], a['order'])):
                if x.get('manual') is not None:
                    continue
                if x['used_conc'] is None or x['used_conc'] >= 100.0 - 1e-12:
                    continue
                old_weight = float(x['weight_g'])
                x['used_conc'] = 100.0
                x['weight_g'] = x['active_g']
                x['forced'] = True
                total_weight -= old_weight - float(x['weight_g'])
                if total_weight <= batch_g + 1e-12:
                    break

    results: list[CalculatedLine] = []
    missing_neat: list[str] = []
    neat_by_name = neat_by_name or {}
    cost_total = 0.0
    for x in provisional:
        line: FormulaLine = x['line']
        if x['weight_g'] is None:
            results.append(CalculatedLine(
                material_id=None, name=line.name, parts=x['parts'], formula_pct=x['formula_pct'],
                inventory_concentration_pct=None, used_concentration_pct=None, weight_g=None,
                active_weight_g=x['active_g'], cost=None, stock_after_g=None,
                forced_neat=False, neat_material_id=None, has_neat_inventory=False,
                inventory_available=False,
            ))
            continue

        cost_rate = max(0.0, float(line.unit_cost_per_g or 0))
        stock_available = float(line.stock_g or 0)
        stock_material_id = line.material_id
        neat_material_id = None
        has_neat = False

        weight_g = float(x['weight_g'])
        # Inventory purchase price/g represents the 100% raw material cost. Predilution only changes
        # how many grams of working stock are weighed, not how much raw aromatic material is consumed.
        # Example: 10 g of a 10% Coumarin stock contains 1 g Coumarin, so cost = 1 g * raw price/g.
        cost = round(float(x['active_g']) * cost_rate, 2)
        cost_total += cost
        stock_after = stock_available - float(x['active_g'])
        results.append(CalculatedLine(
            material_id=stock_material_id, name=line.name, parts=x['parts'], formula_pct=x['formula_pct'],
            inventory_concentration_pct=x['inv_conc'], used_concentration_pct=x['used_conc'],
            weight_g=weight_g, active_weight_g=x['active_g'], cost=cost, stock_after_g=stock_after,
            forced_neat=x['forced'], neat_material_id=neat_material_id, has_neat_inventory=has_neat,
            inventory_available=True,
        ))

    material_cost_total = round(cost_total, 2)
    # Cost the solvent portion from the requested FINISHED strength, independent of the
    # predilution stocks used to manufacture the formula. Example: a 10 g batch at 20%
    # strength has an 8 g solvent cost basis. ``solvent_g`` below remains the practical
    # additional solvent required after accounting for solvent already present in stocks.
    solvent_cost_basis_g = batch_g * max(0.0, 1.0 - load / 100.0)
    solvent_cost = round(solvent_cost_basis_g * max(0.0, float(solvent_unit_cost_per_g or 0)), 2)
    if complete:
        weighed_total = sum(float(x.weight_g or 0) for x in results)
        solvent = max(0.0, batch_g - weighed_total)
        current_strength = (active_total_g / weighed_total * 100.0) if weighed_total > 1e-15 else 0.0
    else:
        weighed_total = None
        solvent = None
        current_strength = None
    total_cost = round(material_cost_total + solvent_cost, 2)

    return ManufacturingPlan(
        lines=results, batch_g=batch_g, fragrance_load_pct=load, active_total_g=active_total_g,
        weighed_material_total_g=weighed_total, solvent_g=solvent,
        current_strength_without_solvent_pct=current_strength, cost_total=total_cost,
        forced_neat_count=sum(1 for x in results if x.forced_neat),
        missing_neat_names=[],
        missing_inventory_names=list(dict.fromkeys(missing_inventory)),
        material_cost_total=material_cost_total, solvent_cost=solvent_cost, solvent_cost_basis_g=solvent_cost_basis_g,
    )


def calculate_formula(lines: Iterable[FormulaLine], batch_g: float) -> list[CalculatedLine]:
    """Legacy stock-solution calculator kept for compatibility with older callers/tests."""
    lines = [x for x in lines if x.inventory_available and x.material_id is not None]
    total = sum(max(0.0, float(x.parts or 0)) for x in lines)
    if total <= 0:
        return []
    out=[]
    for line in lines:
        p=max(0.0,float(line.parts or 0));pct=p/total*100.0;w=float(batch_g)*p/total
        conc=_safe_conc(line.concentration_pct);active=w*conc/100.0
        out.append(CalculatedLine(line.material_id,line.name,p,pct,conc,conc,w,active,
                                  active*max(0.0,float(line.unit_cost_per_g or 0)),float(line.stock_g or 0)-active,
                                  False,None,False,True))
    return out
