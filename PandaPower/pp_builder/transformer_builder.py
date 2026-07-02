"""
transformer_builder.py — creates pandapower 2W and 3W transformers
from the Transformers sheet.

Convention
----------
* Parameters follow create_transformer_from_parameters / create_transformer3w_from_parameters.
"""
from __future__ import annotations

import pandas as pd
import pandapower as pp
import math

from .config import SHEET_2W_TRANSFORMERS, SHEET_3W_TRANSFORMERS
from .excel_reader import _float, _bool, _str


def build_transformers(
    net: pp.pandapowerNet,
    sheets: dict[str, pd.DataFrame],
    bus_map: dict[str, int],
) -> dict[str, int]:
    """
    Create transformer elements from the Transformers sheet.

    Returns
    -------
    dict[str, int]
        trafo_map: {transformer_name → pandapower trafo index}
    """
    df = sheets[SHEET_2W_TRANSFORMERS]
    trafo_map: dict[str, int] = {}
    skipped: list[str] = []


    for _, row in df.iterrows():
        name       = _str(row, "Engedélyesi azonosító")
        hv_name    = _str(row, "Primer")
        lv_name    = _str(row, "Szekunder")
        in_service = _bool(row, "Bent", True)

        # Bus lookup
        if hv_name not in bus_map:
            skipped.append(f"{name} (HVBus '{hv_name}' not found)")
            continue
        if lv_name not in bus_map:
            skipped.append(f"{name} (LVBus '{lv_name}' not found)")
            continue

        hv_bus = bus_map[hv_name]
        lv_bus = bus_map[lv_name]

        # Common parameters
        sn_mva      = _float(row, "Sn",      100.0)
        vn_hv_kv    = _float(row, "Upn",     net.bus.at[hv_bus, "vn_kv"])
        vn_lv_kv    = _float(row, "Usn",     net.bus.at[lv_bus, "vn_kv"])

        Eps     = _float(row, "Eps",  10.0)
        P_rov   = _float(row, "Pröv",  0.0)           # copper losses [kW]
        P_urj   = _float(row, "Pürj",  0.0)           # iron losses [kW]
        Q_urj   = _float(row, "Qürj",  0.0)           # no-load reactive [kVAr]

        vk_percent  = Eps
        if vk_percent >= 20:
            vk_percent = 19.9999
        vkr_percent = (P_rov / (sn_mva*1000)) * 100
        pfe_kw      = P_urj
        S0          = math.sqrt(P_urj**2 + Q_urj**2)  # kVA
        i0_percent  = (S0 / sn_mva * 1000) * 100

        shift_deg = get_shift_degree(_str(row, "Kapcsolási", "YNd11"))

        OLTC        = True
        power_station_unit = True

        # Tap changer (optional columns)
        tap_num         = _float(row, "N", float("nan"))
        tap_side_str    = _str(row, "Szabályzott oldal", "hv") or "hv"
        tap_max_pc        = _float(row, "Umax", float("nan"))
        tap_min_pc         = _float(row, "Umin", float("nan"))
        tap_pos_pc    = _float(row, "Ube", float("nan"))

        if tap_side_str == hv_name:
            tap_side = "hv"
        else:
            tap_side = "lv"
        
        idx = pp.create_transformer_from_parameters(
                net,
                hv_bus=hv_bus, 
                lv_bus=lv_bus,
                sn_mva=sn_mva,
                vn_hv_kv=vn_hv_kv, 
                vn_lv_kv=vn_lv_kv,
                vk_percent=vk_percent, vkr_percent=vkr_percent,
                pfe_kw=pfe_kw, i0_percent=i0_percent,
                shift_degree=shift_deg,             
                in_service=in_service,
                OLTC=OLTC,
                power_station_unit=power_station_unit
            ) 
        
        tap_data_valid = not any(math.isnan(v) for v in [tap_num, tap_min_pc, tap_max_pc, tap_pos_pc])


        if tap_data_valid and tap_num > 1:
            tap_min = 1
            tap_num_int = int(tap_num)
            tap_neutral = math.ceil(tap_num_int / 2)
            tap_max = tap_num
            tap_step_pc = (abs(tap_max_pc) + abs(tap_min_pc)) / (tap_num-1)
            tap_pos = tap_pos_pc / tap_step_pc

            net.trafo.at[idx, "tap_neutral"]       = tap_neutral
            net.trafo.at[idx, "tap_min"]           = tap_min
            net.trafo.at[idx, "tap_max"]           = tap_max
            net.trafo.at[idx, "tap_step_percent"]  = tap_step_pc
            net.trafo.at[idx, "tap_pos"]           = tap_pos
            net.trafo.at[idx, "tap_side"]          = tap_side
            net.trafo.at[idx, "tap_phase_shifter"] = False
        else:
            net.trafo.at[idx, "tap_neutral"]       = 6
            net.trafo.at[idx, "tap_min"]           = 1
            net.trafo.at[idx, "tap_max"]           = 11
            net.trafo.at[idx, "tap_step_percent"]  = tap_step_pc
            net.trafo.at[idx, "tap_pos"]           = 6
            net.trafo.at[idx, "tap_side"]          = tap_side
            net.trafo.at[idx, "tap_phase_shifter"] = False

        
        trafo_map[name] = idx

        
        
    num_2w = len(trafo_map)

    if skipped:
        print(f"[transformer_builder]  WARNING — skipped {len(skipped)}: "
              + "; ".join(skipped))
    print(f"[transformer_builder]  Created {num_2w} 2w transformer(s).")

    df = sheets[SHEET_3W_TRANSFORMERS]
    
    for _, row in df.iterrows():
        name       = _str(row, "Engedélyesi azonosító")
        hv_name    = _str(row, "Primer")
        mv_name    = _str(row, "Szekunder")
        lv_name    = _str(row, "Tercier")
        in_service = _bool(row, "Bent", True)

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

        # Common parameters
        sn_hv_mva      = _float(row, "Sps",      100.0)
        sn_mv_mva      = _float(row, "Sps",      100.0)
        sn_lv_mva      = _float(row, "Sst",      50.0)
        vn_hv_kv    = _float(row, "Upn",     net.bus.at[hv_bus, "vn_kv"])
        vn_mv_kv    = _float(row, "Usn",     net.bus.at[mv_bus, "vn_kv"])
        vn_lv_kv    = _float(row, "Utn",     net.bus.at[lv_bus, "vn_kv"])

        S_base = sn_hv_mva
        Eps     = _float(row, "Eps",  10.0)
        Ept     = _float(row, "Ept",  10.0)
        Est     = _float(row, "Est",  10.0)
        P_rov_ps   = _float(row, "Prps",  0.0)           # copper losses [kW]
        P_rov_pt   = _float(row, "Prpt",  0.0) 
        P_rov_st   = _float(row, "Prst",  0.0)

        eps_ps  = Eps * (S_base / sn_hv_mva)
        eps_pt  = Ept * (S_base / sn_mv_mva)
        eps_st  = Est * (S_base / sn_lv_mva)
        vk_hv_percent  = Eps #0.5 * (eps_ps + eps_pt - eps_st)
        vk_mv_percent = Est #0.5 * (eps_ps + eps_st - eps_pt)
        vk_lv_percent  = Ept #0.5 * (eps_pt + eps_st - eps_ps)
        if vk_hv_percent >= 20:
            vk_hv_percent = 19.99999
        if vk_mv_percent >= 20:
            vk_mv_percent = 19.99999
        if vk_lv_percent >= 20:
            vk_lv_percent = 19.99999
   
        vkr_ps_pair = (P_rov_ps / (sn_hv_mva * 1000)) * 100
        vkr_pt_pair = (P_rov_pt / (sn_mv_mva * 1000)) * 100
        vkr_st_pair = (P_rov_st / (sn_lv_mva * 1000)) * 100
        vkr_ps = vkr_ps_pair * (S_base / sn_hv_mva)
        vkr_pt = vkr_pt_pair * (S_base / sn_mv_mva)
        vkr_st = vkr_st_pair * (S_base / sn_lv_mva)

        if vkr_ps_pair < 1e-6:
            vkr_ps_pair = 0
        if vkr_pt_pair < 1e-6:
            vkr_pt_pair = 0
        if vkr_st_pair < 1e-6:
            vkr_st_pair = 0

        vkr_hv_percent = vkr_ps_pair #0.5 * (vkr_ps + vkr_pt - vkr_st)
        vkr_mv_percent = vkr_st_pair #0.5 * (vkr_ps + vkr_st - vkr_pt)
        vkr_lv_percent = vkr_pt_pair #0.5 * (vkr_pt + vkr_st - vkr_ps)


        P_urj   = _float(row, "Pürj",  0.0)           # iron losses [kW]
        Q_urj   = _float(row, "Qürj",  0.0)           # no-load reactive [kVAr]
      
        pfe_kw      = P_urj
        S0          = math.sqrt(P_urj**2 + Q_urj**2)  # kVA
        i0_percent  = (S0 / sn_mva * 1000) * 100

        OLTC        = True
        power_station_unit = True

        # Tap changer (optional columns)
        tap_num         = _float(row, "N", float("nan"))
        tap_side_str    = _str(row, "Szabályzott oldal", "hv") or "hv"
        tap_max_pc        = row.get("Umax",     float("nan"))
        tap_min_pc         = row.get("Umin",     float("nan"))
        tap_pos_pc    = row.get("Ube", float("nan"))

        if tap_side_str == hv_name:
            tap_side = "hv"
        elif tap_side_str == mv_name:
            tap_side = "mv"
        else:
            tap_side = "lv"
        
        idx = pp.create_transformer3w_from_parameters(
                net,

                hv_bus=hv_bus,
                mv_bus=mv_bus,
                lv_bus=lv_bus,

                sn_hv_mva=sn_hv_mva,
                sn_mv_mva=sn_mv_mva,
                sn_lv_mva=sn_lv_mva,

                vn_hv_kv=vn_hv_kv, 
                vn_mv_kv=vn_mv_kv,
                vn_lv_kv=vn_lv_kv,

                vk_hv_percent=vk_hv_percent,
                vk_mv_percent=vk_mv_percent,
                vk_lv_percent=vk_lv_percent,

                vkr_hv_percent=vkr_hv_percent,
                vkr_mv_percent=vkr_mv_percent,
                vkr_lv_percent=vkr_lv_percent,

                pfe_kw=pfe_kw, 
                i0_percent=i0_percent,

                #shift_mv_degree=shift_mv_deg,   
                #shift_lv_degree=shift_lv_deg,       
                    
                in_service=in_service,
                OLTC=OLTC,
                power_station_unit=power_station_unit
            )

        tap_data_valid = not any(math.isnan(v) for v in [tap_num, tap_min_pc, tap_max_pc, tap_pos_pc])

        if tap_data_valid and tap_num > 1:
            tap_num_int = int(tap_num)
            tap_neutral = math.ceil(tap_num_int / 2)
            tap_min = 1
            tap_max = tap_num
            tap_step_pc = (abs(tap_max_pc) + abs(tap_min_pc)) / (tap_num-1)
            tap_pos = tap_pos_pc / tap_step_pc
            net.trafo.at[idx, "tap_neutral"]       = tap_neutral
            net.trafo.at[idx, "tap_min"]           = tap_min
            net.trafo.at[idx, "tap_max"]           = tap_max
            net.trafo.at[idx, "tap_step_percent"]  = tap_step_pc
            net.trafo.at[idx, "tap_pos"]           = tap_pos
            net.trafo.at[idx, "tap_side"]          = tap_side
            net.trafo.at[idx, "tap_phase_shifter"] = False
        else:
            net.trafo.at[idx, "tap_neutral"]       = 6
            net.trafo.at[idx, "tap_min"]           = 1
            net.trafo.at[idx, "tap_max"]           = 11
            net.trafo.at[idx, "tap_step_percent"]  = tap_step_pc
            net.trafo.at[idx, "tap_pos"]           = 6
            net.trafo.at[idx, "tap_side"]          = tap_side
            net.trafo.at[idx, "tap_phase_shifter"] = False

        trafo_map[name] = idx


    if skipped:
        print(f"[transformer_builder]  WARNING — skipped {len(skipped)}: "
              + "; ".join(skipped))
    print(f"[transformer_builder]  Created {len(trafo_map)-num_2w} 3w transformer(s).")


    """
        leakage_resistance_ratio_hv = leakage_resistance_ratio_hv
        leakage_reactance_ratio_hv = leakage_reactance_ratio_hv
        """

    return trafo_map



VECTOR_GROUP_SHIFT: dict[str, float] = {
    # ── Most common in MAVIR data ─────────────────────────────────────────────
    "YNd11":    330.0,   # -30° (clock 11)
    "YNyn6":    180.0,   # clock 6
    "YNd5":     150.0,   # clock 5
    "Yy0":        0.0,   # clock 0 — no shift
    "Yd11":     330.0,   # clock 11
    "YNy6":     180.0,   # clock 6 (no neutral on LV)

    # ── Less common ───────────────────────────────────────────────────────────
    "YNyn0":      0.0,   # clock 0
    "Dyn5":     150.0,   # delta HV, star LV, clock 5
    "Dyn1":      30.0,   # delta HV, star LV, clock 1
    "Yd5":      150.0,   # clock 5

    # ── Special: in-phase + delta (autotransformer or booster) ───────────────
    "YNyn6+d":  180.0,   # treat as yn6 for load flow purposes
}


def get_shift_degree(vector_group: str) -> float:
    """
    Return the phase shift in degrees for *vector_group*.
    Falls back to 0.0 with a warning for unknown groups.
    """
    # Normalise: strip whitespace, try exact match first
    vg = str(vector_group).strip()
    if vg in VECTOR_GROUP_SHIFT:
        return VECTOR_GROUP_SHIFT[vg]

    # Fuzzy fallback: extract clock number from trailing digits
    import re
    m = re.search(r"(\d+)$", vg)
    if m:
        clock = int(m.group(1))
        shift = (clock * 30) % 360
        print(f"[transformer_builder]  WARNING: unknown vector group "
              f"'{vg}' — derived shift {shift}° from clock {clock}.")
        return float(shift)

    print(f"[transformer_builder]  WARNING: cannot parse vector group "
          f"'{vg}' — defaulting to 0°.")
    return 0.0
