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


def scale_loads_to_target(
    net: pp.pandapowerNet,
    target_mw: float,
    in_service_only: bool = True,
    respect_existing_scaling: bool = True,
) -> float:
    """
    Scale all loads uniformly so that total active load equals *target_mw*.

    The current total is computed as sum(p_mw * scaling) over the loads
    considered (in-service only by default). A single multiplicative
    factor is then applied on top of each load's existing `scaling`
    value, preserving relative differences between loads while hitting
    the requested system-wide capacity.

    Parameters
    ----------
    net : pp.pandapowerNet
        Network whose net.load table will be modified in place.
    target_mw : float
        Desired total active load capacity in MW (e.g. 6400).
    in_service_only : bool
        If True, only in-service loads contribute to the current total
        and only in-service loads are rescaled.
    respect_existing_scaling : bool
        If True, multiply the existing per-load `scaling` values by the
        derived factor (preserves per-load ScalingFactor differences).
        If False, overwrite `scaling` directly with the factor.

    Returns
    -------
    float
        The scale factor that was applied.

    Raises
    ------
    ValueError
        If there are no loads, or the current total load is zero
        (scaling factor would be undefined).
    """
    mask = net.load["in_service"] if in_service_only else pd.Series(True, index=net.load.index)

    if not mask.any():
        raise ValueError("[load_builder] No (in-service) loads found — cannot scale to target.")

    current_total_mw = (net.load.loc[mask, "p_mw"] * net.load.loc[mask, "scaling"]).sum()

    if current_total_mw <= 0:
        raise ValueError(
            f"[load_builder] Current total load is {current_total_mw:.3f} MW — "
            "cannot derive a finite scale factor."
        )

    factor = target_mw / current_total_mw

    if respect_existing_scaling:
        net.load.loc[mask, "scaling"] = net.load.loc[mask, "scaling"] * factor
    else:
        net.load.loc[mask, "scaling"] = factor

    new_total_mw = (net.load.loc[mask, "p_mw"] * net.load.loc[mask, "scaling"]).sum()

    print(
        f"[load_builder] Scaled loads: {current_total_mw:.1f} MW → "
        f"{new_total_mw:.1f} MW (target {target_mw:.1f} MW, factor={factor:.4f})"
    )
    return factor
