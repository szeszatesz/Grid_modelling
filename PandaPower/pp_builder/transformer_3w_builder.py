# pp_builder/transformer_3w_builder.py
"""
Creates pandapower 3W transformers from the 3W Transformers sheet.

Star-circuit decomposition
--------------------------
The measured impedances are between winding *pairs* (ps, pt, st).
The star formula converts them to per-winding (hv, mv, lv) values
referred to a common S_base = sn_hv_mva.
Negative star-circuit vk/vkr values are taken as abs() because
pandapower requires positive impedances for load flow.
"""
from __future__ import annotations

import math
import pandas as pd
import pandapower as pp

from .config import SHEET_3W_TRANSFORMERS
from .excel_reader import _float, _bool, _str
from .vector_groups import get_shift_degree


# ── Star-circuit helpers ──────────────────────────────────────────────────────

def _star_vk(eps_ps, eps_pt, eps_st) -> tuple[float, float, float]:
    """Return (vk_hv, vk_mv, vk_lv) from pair-wise short-circuit voltages."""
    vk_hv = abs(0.5 * (eps_ps + eps_pt - eps_st))
    vk_mv = abs(0.5 * (eps_ps + eps_st - eps_pt))
    vk_lv = abs(0.5 * (eps_pt + eps_st - eps_ps))
    return vk_hv, vk_mv, vk_lv


def _star_vkr(vkr_ps, vkr_pt, vkr_st) -> tuple[float, float, float]:
    """Return (vkr_hv, vkr_mv, vkr_lv) from pair-wise resistive components."""
    vkr_hv = abs(0.5 * (vkr_ps + vkr_pt - vkr_st))
    vkr_mv = abs(0.5 * (vkr_ps + vkr_st - vkr_pt))
    vkr_lv = abs(0.5 * (vkr_pt + vkr_st - vkr_ps))
    return vkr_hv, vkr_mv, vkr_lv


def _clamp_vk(vk: float, vkr: float, label: str) -> tuple[float, float]:
    """Enforce pandapower constraints: vk > 0, vkr < vk."""
    if vk < 1e-4:
        print(f"[transformer_3w_builder]  WARNING: {label} vk≈0 → set to 0.01%")
        vk = 0.01
    if vk >= 20.0:
        vk = 19.9999
    if vkr >= vk:
        vkr = vk * 0.99
        print(f"[transformer_3w_builder]  WARNING: {label} vkr≥vk → clamped to {vkr:.4f}%")
    return vk, vkr


# ── Main builder ──────────────────────────────────────────────────────────────

def build_transformers_3w(
    net: pp.pandapowerNet,
    sheets: dict[str, pd.DataFrame],
    bus_map: dict[str, int],
) -> dict[str, int]:
    """
    Create 3W transformer elements.

    Returns
    -------
    dict[str, int]
        trafo3w_map: {transformer_name → pandapower trafo3w index}
    """
    df = sheets[SHEET_3W_TRANSFORMERS]
    trafo3w_map: dict[str, int] = {}
    skipped:     list[str]      = []

    for _, row in df.iterrows():
        name       = _str(row,  "Engedélyesi azonosító")
        hv_name    = _str(row,  "Primer")
        mv_name    = _str(row,  "Szekunder")
        lv_name    = _str(row,  "Tercier")
        in_service = _bool(row, "Bent", True)

        # ── Bus lookup ─────────────────────────────────────────────────────
        # Bus lookup
        if hv_name not in bus_map:
            skipped.append(f"{name} (HVBus '{hv_name}' not found)")
            continue
        if mv_name not in bus_map:
            skipped.append(f"{name} (LVBus '{lv_name}' not found)")
            continue
        if lv_name not in bus_map:
            skipped.append(f"{name} (LVBus '{lv_name}' not found)")
            continue

        hv_bus = bus_map[hv_name]
        mv_bus = bus_map[mv_name]
        lv_bus = bus_map[lv_name]

        # ── Rated values ───────────────────────────────────────────────────
        sn_hv_mva = _float(row, "Sps", 100.0)
        sn_mv_mva = _float(row, "Sps", sn_hv_mva) 
        sn_lv_mva = _float(row, "Sst",  50.0)

        vn_hv_kv = _float(row, "Upn", net.bus.at[hv_bus, "vn_kv"])
        vn_mv_kv = _float(row, "Usn", net.bus.at[mv_bus, "vn_kv"])
        vn_lv_kv = _float(row, "Utn", net.bus.at[lv_bus, "vn_kv"])

        # ── Short-circuit voltages (pair-wise, %) ──────────────────────────
        S_base = sn_hv_mva

        Eps = _float(row, "Eps", 10.0)
        Ept = _float(row, "Ept", 10.0)
        Est = _float(row, "Est", 10.0)

        # Normalise to common base
        eps_ps = Eps * (S_base / sn_hv_mva)
        eps_pt = Ept * (S_base / sn_mv_mva)
        eps_st = Est * (S_base / sn_lv_mva)

        vk_hv = min(eps_ps, 19.9999)
        vk_mv = min(eps_pt, 19.9999)
        vk_lv = min(eps_st, 19.9999)

        # ── Resistive components ───────────────────────────────────────────
        p_rov_ps = _float(row, "Prps", 0.0)   # copper losses HV-MV [kW]
        p_rov_pt = _float(row, "Prpt", 0.0)   # copper losses HV-LV [kW]
        p_rov_st = _float(row, "Prst", 0.0)   # copper losses MV-LV [kW]

        vkr_ps = (p_rov_ps / (sn_hv_mva * 1000)) * 100 #* (S_base / sn_hv_mva)
        vkr_pt = (p_rov_pt / (sn_mv_mva * 1000)) * 100 #* (S_base / sn_mv_mva)
        vkr_st = (p_rov_st / (sn_lv_mva * 1000)) * 100 #* (S_base / sn_lv_mva)

        vkr_hv = max(vkr_ps, 0)
        vkr_mv = max(vkr_pt, 0)
        vkr_lv = max(vkr_st, 0)

        # ── Clamp to pandapower constraints ────────────────────────────────
        """vk_hv,  vkr_hv  = _clamp_vk(vk_hv,  vkr_hv,  f"{name} HV")
        vk_mv,  vkr_mv  = _clamp_vk(vk_mv,  vkr_mv,  f"{name} MV")
        vk_lv,  vkr_lv  = _clamp_vk(vk_lv,  vkr_lv,  f"{name} LV")"""

        # ── No-load losses ─────────────────────────────────────────────────
        p_urj  = _float(row, "Pürj", 0.0)   # iron losses [kW]
        q_urj  = _float(row, "Qürj", 0.0)   # no-load reactive [kVAr]
        pfe_kw = p_urj
        s0     = math.sqrt(p_urj ** 2 + q_urj ** 2)   # kVA
        i0_percent = (s0 / (sn_hv_mva * 1000)) * 100  # ← use sn_hv_mva, not sn_mva

        # ── Tap changer ────────────────────────────────────────────────────
        tap_num    = _float(row, "N",                  float("nan"))
        tap_side_s = _str(row,  "Szabályzott oldal",   "hv")
        tap_max_pc = _float(row, "Umax",               float("nan"))
        tap_min_pc = _float(row, "Umin",               float("nan"))
        tap_pos_pc = _float(row, "Ube",                float("nan"))

        if tap_side_s == hv_name:
            tap_side = "hv"
        elif tap_side_s == mv_name:
            tap_side = "mv"
        else:
            tap_side = "lv"

        # ── Create element ─────────────────────────────────────────────────
        idx = pp.create_transformer3w_from_parameters(
            net,
            name       = name,
            hv_bus     = hv_bus,
            mv_bus     = mv_bus,
            lv_bus     = lv_bus,
            sn_hv_mva  = sn_hv_mva,
            sn_mv_mva  = sn_mv_mva,
            sn_lv_mva  = sn_lv_mva,
            vn_hv_kv   = vn_hv_kv,
            vn_mv_kv   = vn_mv_kv,
            vn_lv_kv   = vn_lv_kv,
            vk_hv_percent  = vk_hv,
            vk_mv_percent  = vk_mv,
            vk_lv_percent  = vk_lv,
            vkr_hv_percent = vkr_hv,
            vkr_mv_percent = vkr_mv,
            vkr_lv_percent = vkr_lv,
            pfe_kw         = pfe_kw,
            i0_percent     = i0_percent,
            in_service         = in_service,
            oltc               = True,
            power_station_unit = True,
        )

        # ── Write tap data to net.trafo3w (not net.trafo) ──────────────────
        tap_data_valid = not any(
            math.isnan(v) for v in [tap_num, tap_min_pc, tap_max_pc, tap_pos_pc]
        )

        if tap_data_valid and tap_num > 1:
            tap_num_int = int(tap_num)
            tap_neutral = math.ceil(tap_num_int / 2)
            tap_step_pc = (abs(tap_max_pc) + abs(tap_min_pc)) / (tap_num - 1)
            tap_pos     = (tap_pos_pc - tap_min_pc) / tap_step_pc + 1

            net.trafo3w.at[idx, "tap_neutral"]      = tap_neutral
            net.trafo3w.at[idx, "tap_min"]          = 1
            net.trafo3w.at[idx, "tap_max"]          = tap_num
            net.trafo3w.at[idx, "tap_step_percent"] = tap_step_pc
            net.trafo3w.at[idx, "tap_pos"]          = tap_pos
            net.trafo3w.at[idx, "tap_side"]         = tap_side
        else:
            tap_step_pc = (abs(tap_max_pc) + abs(tap_min_pc)) / 10 \
                          if tap_data_valid else 1.5
            net.trafo3w.at[idx, "tap_neutral"]      = 6
            net.trafo3w.at[idx, "tap_min"]          = 1
            net.trafo3w.at[idx, "tap_max"]          = 11
            net.trafo3w.at[idx, "tap_step_percent"] = tap_step_pc
            net.trafo3w.at[idx, "tap_pos"]          = 6
            net.trafo3w.at[idx, "tap_side"]         = tap_side

        trafo3w_map[name] = idx

    if skipped:
        print(f"[transformer_3w_builder]  WARNING — skipped {len(skipped)}: "
              + "; ".join(skipped))
    print(f"[transformer_3w_builder]  Created {len(trafo3w_map)} 3W transformer(s).")

    return trafo3w_map