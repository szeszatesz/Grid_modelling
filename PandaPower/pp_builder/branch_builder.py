"""
branch_builder.py — creates pandapower lines from the Branches sheet.

Key design choices
------------------
* Summer and winter ratings are stored as two extra columns in net.line:
  max_i_ka_summer / max_i_ka_winter.
  The active rating (max_i_ka) is set at build time according to the chosen
  season, and can be swapped via set_line_ratings(net, season).
* create_line_from_parameters is used so no standard-type library is required.
  MVA rating → max_i_ka conversion: I = S / (√3 · V)
"""
from __future__ import annotations

import math
import pandas as pd
import pandapower as pp

from .config import SHEET_BRANCHES, RATING_SEASON_SUMMER, RATING_SEASON_WINTER
from .excel_reader import _float, _bool, _str


def _mva_to_ka(mva: float, kv: float) -> float:
    """Convert MVA rating to kA thermal limit at the given kV level."""
    if kv <= 0 or mva <= 0:
        return 0.001  # pandapower requires > 0
    return mva / (math.sqrt(3) * kv)


def build_branches(
    net: pp.pandapowerNet,
    sheets: dict[str, pd.DataFrame],
    bus_map: dict[str, int],
    season: str = RATING_SEASON_SUMMER,
) -> dict[str, int]:
    """
    Create line elements from the Branches sheet.

    Parameters
    ----------
    season : str
        Which MVA rating to use as the active max_i_ka.
        Either config.RATING_SEASON_SUMMER or RATING_SEASON_WINTER.

    Returns
    -------
    dict[str, int]
        branch_map: {branch_name → pandapower line index}
    """
    df = sheets[SHEET_BRANCHES]
    branch_map: dict[str, int] = {}
    skipped: list[str] = []

    for _, row in df.iterrows():
        name       = _str(row, "Engedélyesi Azonosító")
        from_name  = _str(row, "Végpont1")
        to_name    = _str(row, "Végpont2")
        

        if from_name not in bus_map:
            skipped.append(f"{name} (FromBus '{from_name}' not found)")
            continue
        if to_name not in bus_map:
            skipped.append(f"{name} (ToBus '{to_name}' not found)")
            continue

        from_bus = bus_map[from_name]
        to_bus   = bus_map[to_name]

        r_ohm_per_km = _float(row, "R")
        x_ohm_per_km = _float(row, "X")
        c_nf_per_km  = _float(row, "C", 0.0) * 1000
        length_km    = 1 #Workaround, since MAVIR data is given in Ohm not in Ohm/km _float(row, "LengthKm", 1.0)
        g_us_per_km  = 0.0 #no data given in MAVIR table
        max_i_ka     = _float(row, "Inyár") / 1000
        if max_i_ka == 0:
            max_i_ka = 1e6
            """bb_sw_idx = pp.create_switch(
                net,
                name=name,
                bus=from_bus,
                element=to_bus,
                et="b"
                closed=True

            )"""
        
        parallel     = int(_float(row, "Parallel", 1))
        df           = 1.0
        in_service   = _bool(row, "Bent", True)

        line_idx = pp.create_line_from_parameters(
            net,
            name=name,
            from_bus=from_bus,
            to_bus=to_bus,
            length_km=max(length_km, 0.001),  # avoid zero-length lines
            r_ohm_per_km=r_ohm_per_km,
            x_ohm_per_km=x_ohm_per_km,
            c_nf_per_km=c_nf_per_km,
            g_us_per_km=g_us_per_km,
            max_i_ka=max_i_ka,
            in_service=in_service,
            parallel=parallel,
            df=df
        )

        branch_map[name] = line_idx

    if skipped:
        print(f"[branch_builder]  WARNING — skipped {len(skipped)} branch(es): "
              + "; ".join(skipped))
    print(f"[branch_builder]  Created {len(branch_map)} lines "
          f"(season={season}).")
    return branch_map

