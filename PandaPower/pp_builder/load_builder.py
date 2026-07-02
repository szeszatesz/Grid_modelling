"""
load_builder.py — creates pandapower loads from the Loads sheet.

Scaling
-------
pandapower loads have a built-in `scaling` column.
The value stored in Excel ("ScalingFactor") is written directly.
Before running a seasonal load flow the caller can update
net.load["scaling"] = <seasonal_factor> globally, or use
apply_load_scaling(net, factor) from this module.
"""
from __future__ import annotations

import pandas as pd
import pandapower as pp

from .config import SHEET_LOADS, DEFAULT_LOAD_SCALING
from .excel_reader import _float, _bool, _str


def build_loads(
    net: pp.pandapowerNet,
    sheets: dict[str, pd.DataFrame],
    bus_map: dict[str, int],
) -> dict[str, int]:
    """
    Create load elements from the Loads sheet.

    Returns
    -------
    dict[str, int]
        load_map: {load_name → pandapower load index}
    """
    df = sheets[SHEET_LOADS]
    load_map: dict[str, int] = {}
    skipped: list[str] = []

    for _, row in df.iterrows():
        name       = _str(row, "Engedélyesi azonosító")
        bus_name   = _str(row, "Végpont")
        in_service = _bool(row, "Bent", True)

        if bus_name not in bus_map:
            skipped.append(f"{name} (Bus '{bus_name}' not found)")
            continue

        p_mw    = _float(row, "Sr",  0.0)
        q_mvar  = _float(row, "Sx", 0.0)
        scaling = _float(row, "ScalingFactor", DEFAULT_LOAD_SCALING)

        idx = pp.create_load(
            net,
            bus=bus_map[bus_name],
            p_mw=p_mw,
            q_mvar=q_mvar,
            scaling=scaling,
            name=name,
            in_service=in_service,
        )
        load_map[name] = idx

    if skipped:
        print(f"[load_builder]  WARNING — skipped {len(skipped)} load(s): "
              + "; ".join(skipped))
    print(f"[load_builder]  Created {len(load_map)} loads.")
    return load_map


def apply_load_scaling(net: pp.pandapowerNet, factor: float) -> None:
    """
    Set the pandapower `scaling` column for all loads to *factor*.
    This multiplies the base p_mw / q_mvar during the next runpp() call.
    """
    net.load["scaling"] = factor
    print(f"[load_builder]  Load scaling set to {factor:.4f} for all loads.")
