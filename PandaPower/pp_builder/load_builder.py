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
import math

import pandas as pd
import pandapower as pp

from .config import SHEET_LOADS, DEFAULT_LOAD_SCALING, TECHNOLOGY_BATTERY
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
    Scale non-battery loads uniformly to the requested active-power total.

    Battery loads are excluded from both:
      - The total used to calculate the scaling factor.
      - The rows whose scaling values are modified.

    Therefore, target_mw represents ordinary demand EXCLUDING battery
    charging. Battery operating points remain unchanged.

    Parameters
    ----------
    net : pp.pandapowerNet
        Network modified in place.
    target_mw : float
        Desired total non-battery active load in MW.
        Must be finite and non-negative.
    in_service_only : bool
        If True, only in-service non-battery loads are considered
        and modified.
    respect_existing_scaling : bool
        If True, multiply existing scaling values by a common factor.
        If False, replace scaling values with a common factor derived
        from the unscaled non-battery p_mw total.

    Returns
    -------
    float
        Applied scaling factor.

    Raises
    ------
    ValueError
        If the target is invalid, no eligible loads exist, or the
        reference total is not finite and positive.
    """
    target_mw = float(target_mw)

    if not math.isfinite(target_mw) or target_mw < 0.0:
        raise ValueError(
            "[load_builder] target_mw must be finite and non-negative."
        )

    loads = net.load

    # Identify batteries by technology, with type as a fallback.
    # Accept both scalar labels and single-item list labels.
    battery_mask = pd.Series(False, index=loads.index)

    for column in ("technology", "type"):
        if column not in loads.columns:
            continue

        labels = loads[column].map(
            lambda value: (
                (value[0] if value else None)
                if isinstance(value, list)
                else value
            )
        )

        battery_mask |= (
            labels.eq(TECHNOLOGY_BATTERY).fillna(False)
        )

    # Only ordinary loads participate in target-load scaling.
    mask = ~battery_mask

    if in_service_only:
        mask &= loads["in_service"].fillna(False).astype(bool)

    if not mask.any():
        raise ValueError(
            "[load_builder] No eligible non-battery loads found "
            "— cannot scale to target."
        )

    p_mw = loads.loc[mask, "p_mw"].astype(float)

    if not p_mw.map(math.isfinite).all():
        raise ValueError(
            "[load_builder] Selected loads contain non-finite p_mw."
        )

    if respect_existing_scaling:
        reference_scaling = (
            loads.loc[mask, "scaling"].astype(float)
        )

        if (
            not reference_scaling.map(math.isfinite).all()
            or (reference_scaling < 0.0).any()
        ):
            raise ValueError(
                "[load_builder] Selected loads must have finite, "
                "non-negative scaling values."
            )

    else:
        # Existing scaling is being overwritten, so derive the factor
        # from the unscaled p_mw total, not p_mw * old_scaling.
        reference_scaling = pd.Series(
            1.0,
            index=p_mw.index,
        )

    reference_total_mw = float(
        (p_mw * reference_scaling).sum()
    )

    if (
        not math.isfinite(reference_total_mw)
        or reference_total_mw <= 0.0
    ):
        raise ValueError(
            "[load_builder] Reference non-battery load total must "
            f"be finite and positive; got {reference_total_mw!r} MW."
        )

    factor = target_mw / reference_total_mw
    new_scaling = reference_scaling * factor

    if (
        not math.isfinite(factor)
        or not new_scaling.map(math.isfinite).all()
    ):
        raise ValueError(
            "[load_builder] Calculated scaling is not finite."
        )

    # Battery rows and other excluded rows remain completely untouched.
    loads.loc[mask, "scaling"] = new_scaling

    new_total_mw = float(
        (p_mw * new_scaling).sum()
    )

    print(
        "[load_builder] Scaled non-battery loads: "
        f"reference={reference_total_mw:.1f} MW → "
        f"{new_total_mw:.1f} MW "
        f"(target={target_mw:.1f} MW, factor={factor:.4f}); "
        f"excluded {int(battery_mask.sum())} battery load(s)."
    )

    return factor
