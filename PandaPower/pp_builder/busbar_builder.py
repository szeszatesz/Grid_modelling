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

    for _, row in df.iterrows():
        name       = _str(row, "Azonosító")
        nom_kv_str = name[-6:].strip()
        vn_kv      = float(nom_kv_str)
        if vn_kv == 120.00:
            vn_kv = 132.00
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


    print(f"[busbar_builder]  Created {len(bus_map)} buses, "
          f"{len(slack_buses)} slack/ext_grid(s).")
    
    return bus_map


