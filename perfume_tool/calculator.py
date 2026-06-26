from dataclasses import dataclass
from typing import Union

from .utils import safe_float


@dataclass
class CalcInputs:
    target_weight: float
    default_dilution: float
    desired_dilution: float
    maximum_dilution: float


class PerfumeCalculator:
    def __init__(self, rows: list[dict], inputs: CalcInputs):
        self.rows = rows
        self.inputs = inputs

    @property
    def total_parts(self) -> float:
        return sum(row["part"] for row in self.rows)

    def get_base_dilutions(self) -> list[float]:
        dilutions = []

        for row in self.rows:
            manual = safe_float(row.get("manual_dilution"), None)
            dilutions.append(
                self.inputs.default_dilution if manual is None else manual
            )

        return dilutions

    def possible_perfume_dilution(self, dilutions: list[float]) -> float:
        """
        Strongest possible final perfume dilution based on formula parts
        and stock dilutions.

        Formula:
            possible dilution = total_parts / SUM(part_i / dilution_i)

        Important:
            dilution_i is stored as percent number.
            10 means 10%.
            0.01 means 0.01%.
        """
        total = self.total_parts
        denom = 0.0

        for row, dilution in zip(self.rows, dilutions):
            if dilution <= 0:
                return 0.0

            denom += row["part"] / dilution

        return 0.0 if denom == 0 else total / denom

    def dilution_weight_denominator(self, dilutions: list[float]) -> float:
        """
        Weight allocation denominator.

        If a material is more dilute, you need more stock weight.

        Therefore stock weight uses:
            part_i / applied_dilution_i

        not:
            part_i / total_parts
        """
        denom = 0.0

        for row, dilution in zip(self.rows, dilutions):
            if dilution <= 0:
                continue

            denom += row["part"] / dilution

        return denom

    def sorted_indices_by_part_desc(self) -> list[int]:
        """
        Force Concentrated Materials should apply to the largest formula parts,
        not simply the first rows in the table.

        This prevents tiny trace materials like Geosmin from being forced to
        Maximum Dilution just because they appear near the top.
        """
        return sorted(
            range(len(self.rows)),
            key=lambda i: self.rows[i]["part"],
            reverse=True,
        )

    def calculate_force_concentrated_materials(self) -> tuple[Union[int, str], set[int]]:
        """
        Returns:
            force_n, forced_indices

        force_n:
            0, 1, 2, ... or "Impossible"

        forced_indices:
            row indices that should use maximum dilution

        Important:
            The forced rows are chosen by largest Part first.
        """
        base = self.get_base_dilutions()

        if self.possible_perfume_dilution(base) >= self.inputs.desired_dilution:
            return 0, set()

        sorted_indices = self.sorted_indices_by_part_desc()

        for n in range(1, len(self.rows) + 1):
            test = base.copy()
            forced_indices = set(sorted_indices[:n])

            for i in forced_indices:
                if test[i] < self.inputs.maximum_dilution:
                    test[i] = self.inputs.maximum_dilution

            if self.possible_perfume_dilution(test) >= self.inputs.desired_dilution:
                return n, forced_indices

        return "Impossible", set(sorted_indices)

    def calculate(self) -> dict:
        total = self.total_parts
        force_n, forced_indices = self.calculate_force_concentrated_materials()
        base = self.get_base_dilutions()

        applied = []
        changed_flags = []

        for idx, d in enumerate(base):
            changed = (
                isinstance(force_n, int)
                and force_n > 0
                and idx in forced_indices
                and d < self.inputs.maximum_dilution
            )

            applied_dilution = self.inputs.maximum_dilution if changed else d

            applied.append(applied_dilution)
            changed_flags.append(changed)

        current_possible = self.possible_perfume_dilution(applied)
        output_rows = []

        if current_possible < self.inputs.desired_dilution:
            material_blend_weight = self.inputs.target_weight
            additional_solvent = "TOO MUCH DILUTION"
            net_weight = self.inputs.target_weight
        else:
            material_blend_weight = (
                self.inputs.target_weight
                * self.inputs.desired_dilution
                / current_possible
            )

            additional_solvent = max(
                0.0,
                self.inputs.target_weight - material_blend_weight,
            )

            net_weight = material_blend_weight + additional_solvent

        stock_denom = self.dilution_weight_denominator(applied)

        for row, dilution, changed in zip(self.rows, applied, changed_flags):
            pure_fraction = row["part"] / total if total else 0.0

            if stock_denom > 0 and dilution > 0:
                stock_fraction = (row["part"] / dilution) / stock_denom
            else:
                stock_fraction = 0.0

            stock_weight = material_blend_weight * stock_fraction

            output_rows.append(
                {
                    "material": row["material"],

                    # Pure-material-equivalent formula part.
                    "part": row["part"],

                    # Pure material percentage in formula.
                    "raw_pct": pure_fraction * 100,

                    "manual_dilution": row.get("manual_dilution", ""),
                    "applied_dilution": dilution,

                    # Actual diluted stock weight to weigh.
                    "weight": stock_weight,

                    # True when Force Concentrated Materials changed dilution.
                    "topn_changed": changed,

                    # True when imported part was converted from parsed dilution.
                    "part_adjusted_by_dilution": row.get(
                        "part_adjusted_by_dilution",
                        False,
                    ),

                    "parsed_part": row.get("parsed_part", row["part"]),
                    "parsed_dilution": row.get(
                        "parsed_dilution",
                        row.get("manual_dilution", ""),
                    ),
                }
            )

        return {
            "total_parts": total,
            "force_n": force_n,
            "current_possible": current_possible,
            "additional_solvent": additional_solvent,
            "net_weight": net_weight,
            "rows": output_rows,
        }