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

from .config import (
    SHEET_GENERATORS,
    DEFAULT_GEN_SCALING,
    TECH_SCALING_DEFAULTS,
    RATING_SEASON_SUMMER,
    TECHNOLOGY_WIND, TECHNOLOGY_SOLAR, TECHNOLOGY_BATTERY, VOLTAGE_CONTROL_TECHS
)
from .excel_reader import _float, _bool, _str

# Technologies that should be modelled as PQ static generators
_PQ_TECHNOLOGIES = {TECHNOLOGY_WIND, TECHNOLOGY_SOLAR, TECHNOLOGY_BATTERY}


def _get_scale(row: pd.Series, technology: str, season: str) -> float:
    """Resolve scaling factor: Excel override → config default → 1.0."""
    explicit = row.get("ScalingFactor", None)
    if explicit is not None and not pd.isna(explicit):
        return float(explicit)
    return TECH_SCALING_DEFAULTS.get(technology, {}).get(season, DEFAULT_GEN_SCALING)


def build_generators(
    net: pp.pandapowerNet,
    sheets: dict[str, pd.DataFrame],
    bus_map: dict[str, int],
    season: str = RATING_SEASON_SUMMER,
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
    gen_map: dict[str, tuple[str, int]] = {}
    skipped: list[str] = []
    ext_grid: list[str] = []

    for _, row in df.iterrows():
        name       = _str(row, "Engedélyesi azonosító")
        bus_name   = _str(row, "Végpont")
        technology = _str(row, "Technológia", "gas")
        in_service = _bool(row, "Bent", True) 

        if bus_name not in bus_map:
            skipped.append(f"{name} (Bus '{bus_name}' not found)")
            continue

        if bus_name[0] == "X":
            ext_grid.append(f"{name} (Bus '{bus_name}' is external grid)")
            continue

        bus_idx = bus_map[bus_name]
        p_mw    = _float(row, "P",  0.0)
        q_mvar  = _float(row, "Q",  0.0)
        vm_pu   = _float(row, "Uszab",  1.0) / net.bus.at[bus_map[bus_name], "vn_kv"]
        sn_mva  = _float(row, "MVA", float("nan"))
        scaling = _get_scale(row, technology, season)

        max_q_mvar = _float(row, "Qmax",  0.0)
        min_q_mvar = _float(row, "Qmin",  0.0)

        max_p_mw   = _float(row, "Pmax",  0.0)
        min_p_mw   = _float(row, "Pmin",  0.0)

        if technology in VOLTAGE_CONTROL_TECHS and sn_mva is not None and sn_mva > 10 and ((max_q_mvar is not None and max_q_mvar != 0) or (min_q_mvar is not None and min_q_mvar != 0)):
            # Static generator — PQ injection, no voltage control
            # Synchronous / voltage-controlled generator — PV node
            idx = pp.create_gen(
                net,
                bus=bus_idx,
                p_mw=p_mw,
                vm_pu=vm_pu,
                sn_mva=sn_mva,
                scaling=scaling,
                name=name,
                type=technology,
                in_service=in_service,
                max_q_mvar = max_q_mvar,
                min_q_mvar = min_q_mvar,
                max_p_mw   = max_p_mw,
                min_p_mw   = min_p_mw,
            )
            # Store technology for later use by apply_gen_scaling
            net.gen.at[idx, "technology"] = technology
            gen_map[name] = ("gen", idx)
        else:
            idx = pp.create_sgen(
                net,
                bus=bus_idx,
                p_mw=p_mw,
                q_mvar=q_mvar,
                sn_mva=sn_mva,
                scaling=scaling,
                name=name,
                type=technology,
                in_service=in_service,
            )
            # Store technology for later use by apply_gen_scaling
            net.sgen.at[idx, "technology"] = technology
            gen_map[name] = ("sgen", idx)
            

    if skipped:
        print(f"[generator_builder]  WARNING — skipped {len(skipped)}: "
              + "; ".join(skipped))
        
    if ext_grid:
        print(f"[generator_builder]  omitted {len(ext_grid)} external grids")

    n_sgen = sum(1 for v in gen_map.values() if v[0] == "sgen")
    n_gen  = sum(1 for v in gen_map.values() if v[0] == "gen")
    print(f"[generator_builder]  Created {n_gen} gen (PV) + "
          f"{n_sgen} sgen (PQ), season={season}.")
    return gen_map


def apply_gen_scaling(
    net: pp.pandapowerNet,
    season: str,
    tech_overrides: dict[str, float] | None = None,
) -> None:
    """
    Update the `scaling` column in net.gen and net.sgen for all generators
    based on per-technology factors for the given season.

    Parameters
    ----------
    season : str
        RATING_SEASON_SUMMER or RATING_SEASON_WINTER.
    tech_overrides : dict, optional
        {technology_label: scale_factor} — overrides config defaults
        for specific technologies in this call only.
    """
    overrides = tech_overrides or {}

    def _scale(technology: str) -> float:
        if technology in overrides:
            return overrides[technology]
        return TECH_SCALING_DEFAULTS.get(technology, {}).get(season, DEFAULT_GEN_SCALING)

    if "technology" in net.gen.columns:
        net.gen["scaling"] = net.gen["technology"].apply(_scale)

    if "technology" in net.sgen.columns:
        net.sgen["scaling"] = net.sgen["technology"].apply(_scale)

    print(f"[generator_builder]  Generator scaling updated for season={season}.")
