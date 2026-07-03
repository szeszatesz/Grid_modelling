# pp_builder/transformer_builder.py
"""
Creates pandapower 2W transformers from the 2W Transformers sheet.
"""
from __future__ import annotations

import math
import pandas as pd
import pandapower as pp

from .config import SHEET_2W_TRANSFORMERS
from .excel_reader import _float, _bool, _str
from .vector_groups import get_shift_degree


def build_transformers(
    net: pp.pandapowerNet,
    sheets: dict[str, pd.DataFrame],
    bus_map: dict[str, int],
) -> dict[str, int]:
    """
    Create 2W transformer elements.

    Returns
    -------
    dict[str, int]
        trafo_map: {transformer_name → pandapower trafo index}
    """
    df = sheets[SHEET_2W_TRANSFORMERS]
    trafo_map: dict[str, int] = {}
    skipped:   list[str]      = []

    for _, row in df.iterrows():
        name       = _str(row,  "Engedélyesi azonosító")
        hv_name    = _str(row,  "Primer")
        lv_name    = _str(row,  "Szekunder")
        in_service = _bool(row, "Bent", True)

        if hv_name not in bus_map:
            skipped.append(f"{name} (HV bus '{hv_name}' not found)")
            continue
        if lv_name not in bus_map:
            skipped.append(f"{name} (LV bus '{lv_name}' not found)")
            continue

        hv_bus = bus_map[hv_name]
        lv_bus = bus_map[lv_name]

        sn_mva   = _float(row, "Sn",  100.0)
        vn_hv_kv = _float(row, "Upn", net.bus.at[hv_bus, "vn_kv"])
        vn_lv_kv = _float(row, "Usn", net.bus.at[lv_bus, "vn_kv"])

        eps   = _float(row, "Eps",  10.0)
        p_rov = _float(row, "Pröv",  0.0)   # copper losses [kW]
        p_urj = _float(row, "Pürj",  0.0)   # iron losses [kW]
        q_urj = _float(row, "Qürj",  0.0)   # no-load reactive [kVAr]

        vk_percent  = min(eps, 19.9999)
        vkr_percent = (p_rov / (sn_mva * 1000)) * 100
        pfe_kw      = p_urj
        s0          = math.sqrt(p_urj ** 2 + q_urj ** 2)   # kVA
        i0_percent  = (s0 / (sn_mva * 1000)) * 100

        shift_deg = get_shift_degree(_str(row, "Kapcsolási", "YNd11"))

        # ── Tap changer ────────────────────────────────────────────────────
        tap_num    = _float(row, "N",                  float("nan"))
        tap_side_s = _str(row,  "Szabályzott oldal",   "")
        tap_max_pc = _float(row, "Umax",               float("nan"))
        tap_min_pc = _float(row, "Umin",               float("nan"))
        tap_pos_pc = _float(row, "Ube",                float("nan"))

        tap_side = "hv" if tap_side_s == hv_name else "lv" if tap_side_s == lv_name else ""

        idx = pp.create_transformer_from_parameters(
            net,
            name       = name,
            hv_bus     = hv_bus,
            lv_bus     = lv_bus,
            sn_mva     = sn_mva,
            vn_hv_kv   = vn_hv_kv,
            vn_lv_kv   = vn_lv_kv,
            vk_percent  = vk_percent,
            vkr_percent = vkr_percent,
            pfe_kw      = pfe_kw,
            i0_percent  = i0_percent,
            shift_degree       = shift_deg,
            in_service         = in_service,
            oltc               = True,
            power_station_unit = True,
        )

        tap_data_valid = not any(
            math.isnan(v) for v in [tap_num, tap_min_pc, tap_max_pc, tap_pos_pc]
        )

        if tap_data_valid and tap_num > 1:
            tap_num_int = int(tap_num)
            tap_neutral = math.ceil(tap_num_int / 2)
            tap_step_pc = (abs(tap_max_pc) + abs(tap_min_pc)) / (tap_num - 1)
            tap_pos     = (tap_pos_pc - tap_min_pc) / tap_step_pc + 1

            net.trafo.at[idx, "tap_neutral"]      = tap_neutral
            net.trafo.at[idx, "tap_min"]          = 1
            net.trafo.at[idx, "tap_max"]          = tap_num
            net.trafo.at[idx, "tap_step_percent"] = tap_step_pc
            net.trafo.at[idx, "tap_pos"]          = tap_pos
            net.trafo.at[idx, "tap_side"]         = tap_side
        else:
            # Fallback: 11-step ±10 % tap changer centred at position 6
            tap_step_pc = (abs(tap_max_pc) + abs(tap_min_pc)) / 10 \
                          if tap_data_valid else 1.5
            net.trafo.at[idx, "tap_neutral"]      = 6
            net.trafo.at[idx, "tap_min"]          = 1
            net.trafo.at[idx, "tap_max"]          = 11
            net.trafo.at[idx, "tap_step_percent"] = tap_step_pc
            net.trafo.at[idx, "tap_pos"]          = 6
            net.trafo.at[idx, "tap_side"]         = tap_side

        trafo_map[name] = idx

    if skipped:
        print(f"[transformer_builder]  WARNING — skipped {len(skipped)}: "
              + "; ".join(skipped))
    print(f"[transformer_builder]  Created {len(trafo_map)} 2W transformer(s).")

    return trafo_map