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
* Intra-substation coupler branches (bus-tie/short-jumper lines whose
  'Engedélyesi Azonosító' name ends in "*S" or "SF") were found to have
  their 'Inyár' rating entered directly in kA in the source sheet, instead
  of A like the regular transmission lines. Applying the standard /1000
  A→kA conversion to those rows silently shrinks the rating by another
  factor of 1000, producing thermally impossible loading percentages
  (e.g. >1000%). _fix_coupler_rating_unit() detects and corrects this.
"""
from __future__ import annotations

import math
import pandas as pd
import pandapower as pp
import json

from .config import SHEET_BRANCHES, RATING_SEASON_SUMMER, RATING_SEASON_WINTER
from .excel_reader import _float, _bool, _str
from .geo_utils import load_line_geodata_from_eov

# Suffixes identifying intra-substation coupler/busbar-tie branches whose
# 'Inyár' rating was entered in kA rather than A in the source data.
_COUPLER_AG_CODES = ("*S", "SF", "S")

def build_branches(
    net: pp.pandapowerNet,
    sheets: dict[str, pd.DataFrame],
    bus_map: dict[str, int],
    line_geo: dict[str, list[tuple[float, float]]],
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
    unit_fixed: list[str] = []

    coording = False
    if coording:
        coords = line_geo.get(line_name)

        # Fallback: case-insensitive substring search
        if coords is None:
            name_lc = line_name.lower()
            for k, v in line_geo.items():
                if k.lower() in name_lc or name_lc in k.lower():
                    coords = v
                    break

        if coords is None:
            return

        # pandapower stores line geodata as a JSON-encoded list of [lon, lat] pairs
        # in net.line_geodata["coords"]
        net.line_geodata.at[line_idx, "coords"] = json.dumps(
            [[lon, lat] for lat, lon in coords]
        )

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

        raw_inyar = _float(row, "Inyár")
        if _str(row, "Ág") in _COUPLER_AG_CODES:
            raw_inyar *= 1000.0
            unit_fixed.append(name)
        max_i_ka = raw_inyar / 1000
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
    if unit_fixed:
        print(f"[branch_builder]  Corrected kA/A unit mix-up on {len(unit_fixed)} "
              f"coupler branch(es): " + "; ".join(unit_fixed))
    print(f"[branch_builder]  Created {len(branch_map)} lines "
          f"(season={season}).")
    return branch_map
