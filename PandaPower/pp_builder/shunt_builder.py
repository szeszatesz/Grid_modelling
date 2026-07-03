# pp_builder/shunt_builder.py
"""
Builder for shunts and MSCDN steps from the shunt/MSCDN Excel sheet.

Column mapping (Hungarian → internal):
    Végpont              → bus name  (first part, strip voltage suffix)
    Engedélyesi azonosító → element name (full licence ID)
    Csoport              → step number (int) — used to group MSCDN steps
    Bent                 → in_service (1=True, 0=False)
    N                    → number of steps (db)
    S                    → capacity per step (MVAr/step, signed)
    Yz                   → susceptance per step (µS/step) — MSCDN only
    So                   → currently switched capacity (MVAr); if non-zero
                           → determines how many steps are in service

pandapower element choice:
    Simple shunt  (Yz == 0 or NaN, S < 0)  → pp.create_shunt (fixed reactor/cap)
    MSCDN step    (Yz != 0)                 → pp.create_shunt with b_pu from Yz,
                                              one pp.shunt row per step,
                                              step in_service driven by So
"""

import math
import pandas as pd
import pandapower as pp
from pp_builder.excel_reader import _float, _str, _int
from pp_builder.config import SHEET_SHUNTS


def build_shunts(
    net: pp.pandapowerNet,
    sheets: dict[str, pd.DataFrame],
    bus_map: dict[str, int],
) -> dict[str, int]:
    """
    Create shunt elements from the shunt/MSCDN sheet.

    Each row in df is one step of a shunt device. Simple shunts
    normally have N=1 (one step). MSCDNs have multiple steps with
    individual Csoport (group/step) numbers, all sharing the same
    Végpont (bus).

    Returns
    -------
    shunt_map : dict
        { licence_id_string : pandapower shunt index }
    """
    df = sheets[SHEET_SHUNTS]
    shunt_map = {}
    skipped   = []

    for _, row in df.iterrows():
        bus_name    = _str(row, "Végpont")
        name        = _str(row, "Engedélyesi azonosító")
        in_service  = bool(_int(row, "Bent", 1))
        n_steps     = _int(row, "N", 1)
        s_per_step  = _float(row, "S", float("nan"))   # MVAr/step (signed)
        yz_us       = _float(row, "Yz", 0.0)           # µS/step
        so_mvar     = _float(row, "So", 0.0)           # currently switched MVAr

        
        if bus_name not in bus_map:
            skipped.append((bus_name, f"bus '{bus_name}' not found"))
            continue

        bus_idx = bus_map[bus_name]
        vn_kv   = net.bus.at[bus_idx, "vn_kv"]

        if math.isnan(s_per_step):
            skipped.append((bus_name, "S column missing"))
            continue

        # ── Determine in_service for this individual step ──────────────────
        # If So is given and non-zero, work out how many steps are active.
        # Steps with step_no <= active_steps are in service.
        step_in_service = in_service
        if not math.isnan(so_mvar) and so_mvar != 0.0 and s_per_step != 0.0:
            active_steps    = round(abs(so_mvar) / abs(s_per_step))
            step_in_service = in_service and (n_steps <= active_steps)

        # ── Choose element type ────────────────────────────────────────────
        is_mscdn = (not math.isnan(yz_us)) and (abs(yz_us) > 1e-9)

        if is_mscdn:
            # MSCDN: susceptance from Yz column (µS → p.u.)
            # S column is the parallel capacitor stage (MVAr) — also reflected
            # as a q_mvar on the shunt for consistent power accounting.
            b_pu   = _us_to_b_pu(yz_us, vn_kv)
            q_mvar = s_per_step          # MVAr component (positive = capacitive)
        else:
            # Plain reactor or fixed capacitor bank — use Q directly
            b_pu   = _mvar_to_b_pu(s_per_step, vn_kv)
            q_mvar = s_per_step
           

        try:
            idx = pp.create_shunt(
                net,
                bus        = bus_idx,
                q_mvar     = q_mvar,          # positive = capacitive
                p_mw       = 0.0,
                name       = name,
                in_service = step_in_service,
            )
            # Store extra metadata as custom columns for reference
            net.shunt.at[idx, "b_pu_rated"]   = b_pu
            net.shunt.at[idx, "n_steps"]       = n_steps
            net.shunt.at[idx, "is_mscdn"]      = is_mscdn
            net.shunt.at[idx, "vn_kv"]         = vn_kv

            shunt_map[name] = idx

        except Exception as exc:
            skipped.append((name, str(exc)))

    # ── Report ────────────────────────────────────────────────────────────
    reactors = net.shunt[net.shunt["is_mscdn"] == False] if "is_mscdn" in net.shunt.columns else net.shunt
    mscdns   = net.shunt[net.shunt["is_mscdn"] == True]  if "is_mscdn" in net.shunt.columns else pd.DataFrame()

    print(f"[shunt_builder]  Created {len(shunt_map)} shunt elements "
          f"({len(mscdns)} MSCDN steps, {len(reactors)} fixed shunts)")

    if skipped:
        print(f"[shunt_builder]  Skipped {len(skipped)} rows:")
        for name, reason in skipped:
            print(f"    {name}: {reason}")

    return shunt_map



# ── helpers ──────────────────────────────────────────────────────────────────

def _mvar_to_b_pu(q_mvar: float, vn_kv: float, sn_mva: float = 100.0) -> float:
    """
    Convert reactive power in MVAr at a given voltage level to p.u. susceptance
    on a 100 MVA base.
        b_pu = Q_Mvar / S_base   (positive b → capacitive, negative → inductive)
    pandapower shunt sign convention: q_mvar > 0 capacitive, < 0 inductive.
    b_pu is used the same way.
    """
    return q_mvar / sn_mva


def _us_to_b_pu(yz_us: float, vn_kv: float, sn_mva: float = 100.0) -> float:
    """
    Convert susceptance from µS (at given vn_kv) to p.u. on 100 MVA base.
        Z_base = vn_kv² / sn_mva   [Ω]
        B_pu   = Yz_S * Z_base      (where Yz_S = Yz_µS * 1e-6)
    """
    z_base = (vn_kv ** 2) / sn_mva
    return yz_us * 1e-6 * z_base
