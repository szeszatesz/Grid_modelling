"""
generator_builder.py — creates pandapower generators / static generators.

Generator type mapping
----------------------
* Voltage-controlled (PV node): pp.create_gen   → for synchronous machines,
  hydro, gas — any technology that regulates terminal voltage.
* PQ (constant P+Q injection): pp.create_sgen  → wind, solar, battery —
  converter-connected, no voltage control.

Per-technology scaling
----------------------
pandapower has a `scaling` column on both gen and sgen tables that multiplies
p_mw in the power flow.  The scaling factor is set per row from:
  1. Excel column "ScalingFactor"   (explicit override, highest priority)
  2. TECH_SCALING_DEFAULTS[technology][season]  (config default)
  3. 1.0 (fallback)

Season is passed at build time.  To switch season without rebuilding, call
apply_gen_scaling(net, season) from this module.
"""
from __future__ import annotations

import pandas as pd
import pandapower as pp

from .config import SHEET_GENERATORS

from .excel_reader import _float, _bool, _str

def build_ext_grids(
    net: pp.pandapowerNet,
    sheets: dict[str, pd.DataFrame],
    bus_map: dict[str, int],
) -> dict[str, int]:
    """
    Create gen / sgen elements from the Generators sheet.

    Returns
    -------
    dict[str, int]
        gen_map: {generator_name → (table, index)}
        where table is "gen" or "sgen".
    """
    df = sheets[SHEET_GENERATORS]
    ext_grid_map: dict[str, int] = {}
    skipped: list[str] = []
    gen: list[str] = []

    for _, row in df.iterrows():
        name       = _str(row, "Engedélyesi azonosító")
        bus_name   = _str(row, "Végpont")
        
        in_service = _bool(row, "Bent", True) 

        if bus_name not in bus_map:
            skipped.append(f"{name} (Bus '{bus_name}' not found)")
            continue

        if bus_name[0] != "X":
            gen.append(f"{name} (Bus '{bus_name}' is external grid)")
            continue

        bus_idx = bus_map[bus_name]
        p_mw    = _float(row, "P",  0.0)
        vm_pu   = _float(row, "Uszab",  1.0) / net.bus.at[bus_map[bus_name], "vn_kv"]
        sn_mva  = _float(row, "MVA", float("nan"))

        max_q_mvar = _float(row, "Qmax",  0.0)
        min_q_mvar = _float(row, "Qmin",  0.0)

        max_p_mw   = _float(row, "Pmax",  0.0)
        min_p_mw   = _float(row, "Pmin",  0.0)

        ext_grid_idx = pp.create_ext_grid(
            net,
            bus=bus_idx,
            p_mw=p_mw,
            vm_pu=vm_pu,
            sn_mva=sn_mva,
            name=name,
            in_service=in_service,
            max_q_mvar = max_q_mvar,
            min_q_mvar = min_q_mvar,
            max_p_mw   = max_p_mw,
            min_p_mw   = min_p_mw,
            slack_weight=1,
            controllable=True
        )

        ext_grid_map[name] = ext_grid_idx
            

    if skipped:
        print(f"[ext_grid_builder]  WARNING — skipped {len(skipped)}: "
              + "; ".join(skipped))
        
    if gen:
        print(f"[ext_grid_builder]  skipped {len(gen)} generators")

    print(f"[ext_grid_builder]  Created {len(ext_grid_map)} external grids")
    
    return ext_grid_map


