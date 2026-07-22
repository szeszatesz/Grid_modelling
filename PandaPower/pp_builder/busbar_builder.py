"""
busbar_builder.py — creates pandapower buses from the Busbars sheet.

pandapower bus:
    pp.create_bus(net, vn_kv, name, in_service, type)
    pp.create_ext_grid(net, bus, vm_pu, name) for slack buses
"""
from __future__ import annotations

import pandapower as pp
import pandas as pd

from .config import SHEET_BUSBARS, SHEET_GENERATORS
from .excel_reader import _float, _bool, _str


def build_busbars(
    net: pp.pandapowerNet,
    sheets: dict[str, pd.DataFrame],
    coord_map: dict[str, tuple[float, float]] | None = None,
) -> dict[str, int]:
    """
    Create buses and external grids (slack nodes) from the Busbars sheet.

    Returns
    -------
    dict[str, int]
        bus_map: {busbar_name → pandapower bus index}
    """
    df = sheets[SHEET_BUSBARS]
    bus_map: dict[str, int] = {}
    slack_buses: list[tuple[int, float]] = []  # (bus_idx, vm_pu)
    skipped: list[str] = []
      
    
    from collections import defaultdict

    _coord_index: dict[str, list] = defaultdict(list)
    if coord_map is not None:
        for cname, (clat, clon) in coord_map.items():
            # extract voltage from name (last whitespace-separated token)
            try:
                cvn = float(cname.split()[-1])
            except ValueError:
                cvn = 0.0
            if cvn == 120.00:
                cvn = 132.00
            _coord_index[cname[:5]].append((cname, clat, clon, cvn))

    
    total_entries = sum(len(v) for v in _coord_index.values())
    print(f"Unique prefixes : {len(_coord_index)}")
    print(f"Total entries   : {total_entries}")   # should be 888
    # ── per-bus geodata assignment ─────────────────

    # Small layout offsets (degrees) — adjust to taste
    _H_STEP  = 0.0002   # horizontal nudge per same-vn neighbour   (~200 m)
    _V_STEP  = 0.0003   # vertical nudge per kV tier difference    (~330 m)

    # Track how many same-prefix/same-vn buses have already been placed
    # so each new one gets a slightly larger nudge.
    _nudge_counter: dict[str, int] = defaultdict(int)
    exact_match = 0

    for _, row in df.iterrows():
        name       = _str(row, "Azonosító")
        vn_kv      = voltage_reader(name)
        zone       = _str(row, "Zóna", None)
        vm_pu      = _float(row, "U", 1.0) / vn_kv   # optional column

        if coord_map is not None:
            lat, lon = coord_map.get(name, (48.7071054341234, 21.2441692731272))

        if lat != 48.7071054341234:
            # ── exact match ───────────────────────────────────────────────────
            exact_match += 1

        else:
            # ── fuzzy match on first 5 characters ────────────────────────────
            prefix    = name[:5]
            candidates = _coord_index.get(prefix, [])

            if not candidates:
                skipped.append(name)
            else:
                # Pick the candidate whose voltage is closest to this bus's vn_kv
                best = min(candidates, key=lambda c: abs(c[3] - vn_kv))
                _, base_lat, base_lon, ref_vn = best

                nudge_key = f"{prefix}_{ref_vn:.3f}_{vn_kv:.3f}"
                step_n    = _nudge_counter[nudge_key]
                _nudge_counter[nudge_key] += 1

                if abs(vn_kv - ref_vn) < 0.5:
                    # Same voltage level → nudge horizontally
                    # Alternate left/right: even steps go right, odd go left
                    direction = 1 if step_n % 2 == 0 else -1
                    offset    = direction * _H_STEP * (step_n // 2 + 1)

                    lon   = base_lon + offset
                    lat   = base_lat 
                else:
                    # Different voltage level → nudge vertically
                    # Higher voltage → above (larger lat), lower → below
                    sign    =  1.0 if vn_kv > ref_vn else -1.0
                    offset  = sign * _V_STEP * (step_n + 1)


                    lon = base_lon 
                    lat = base_lat + offset

        bus_idx = pp.create_bus(
            net,
            vn_kv=vn_kv,
            name=name,
            in_service=True,
            type="b",           # busbar type
            zone=zone,
            geodata=(lon, lat) 
        )
        bus_map[name] = bus_idx
       

    if skipped:
        print(f"[busbar_builder]  No coordinates for {len(skipped)} bus(es): "
              + ", ".join(skipped[:10]) + ("…" if len(skipped) > 10 else ""))
        
    print(f"[busbar_builder]  Created {len(bus_map)} buses, {exact_match} with exact match"
          f"{len(slack_buses)} slack/ext_grid(s).")
    
    return bus_map


def voltage_reader(name):
    """
    Read the nominal voltage from the busbar name.

    The last 6 characters of the name are expected to be the voltage in kV,
    e.g. "Busbar_132.00" → 132.0 kV.
    """
    nom_kv_str = name[-6:].strip()
    vn_kv      = float(nom_kv_str)
    if vn_kv == 120.00:
        vn_kv = 132.00
    return vn_kv



