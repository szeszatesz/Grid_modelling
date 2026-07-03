# pp_builder/vector_groups.py
"""Vector group → phase shift lookup shared by 2W and 3W transformer builders."""
from __future__ import annotations
import re

VECTOR_GROUP_SHIFT: dict[str, float] = {
    "YNd11":   330.0,
    "YNyn6":   180.0,
    "YNd5":    150.0,
    "Yy0":       0.0,
    "Yd11":    330.0,
    "YNy6":    180.0,
    "YNyn0":     0.0,
    "Dyn5":    150.0,
    "Dyn1":     30.0,
    "Yd5":     150.0,
    "YNyn6+d": 180.0,
}

def get_shift_degree(vector_group: str) -> float:
    vg = str(vector_group).strip()
    if vg in VECTOR_GROUP_SHIFT:
        return VECTOR_GROUP_SHIFT[vg]
    m = re.search(r"(\d+)$", vg)
    if m:
        clock = int(m.group(1))
        shift = (clock * 30) % 360
        print(f"[vector_groups]  WARNING: unknown group '{vg}' → {shift}° (clock {clock})")
        return float(shift)
    print(f"[vector_groups]  WARNING: cannot parse '{vg}' → defaulting to 0°")
    return 0.0