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

    if coord_map is not None and "geodata" not in net.bus.columns:
        net.bus["geodata"] = None
        net.bus["geodata"] = net.bus["geodata"].astype(object)

    for _, row in df.iterrows():
        name       = _str(row, "Azonosító")
        vn_kv      = voltage_reader(name)
        zone       = _str(row, "Zóna", None)
        vm_pu      = _float(row, "U", 1.0) / vn_kv   # optional column

        bus_idx = pp.create_bus(
            net,
            vn_kv=vn_kv,
            name=name,
            in_service=True,
            type="b",           # busbar type
            zone=zone
        )
        bus_map[name] = bus_idx

        # ── Geodata ────────────────────────────────────────────────────────
        if coord_map is not None:
            lat, lon = coord_map.get(name, (None, None))
            if lat is not None:
                net.bus.at[bus_idx, "geodata"] = (lon, lat)   # pandapower: x = longitude, y = latitude
            else:
                skipped.append(name)

    if skipped:
        print(f"[busbar_builder]  No coordinates for {len(skipped)} bus(es): "
              + ", ".join(skipped[:10]) + ("…" if len(skipped) > 10 else ""))
        
    print(f"[busbar_builder]  Created {len(bus_map)} buses, "
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



