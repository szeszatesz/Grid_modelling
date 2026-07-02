"""
network_topology.py — island detection and topology diagnostics
for pandapower networks (mirrors the PyIPSA topology utilities).
"""
from __future__ import annotations

import collections
import networkx as nx
import pandas as pd
import pandapower as pp


def build_nx_graph(net: pp.pandapowerNet, in_service_only: bool = True) -> nx.Graph:
    """
    Build a NetworkX undirected graph from a pandapower network.
    Only in-service elements are included when in_service_only=True.
    """
    G = nx.Graph()
    for idx, row in net.bus.iterrows():
        G.add_node(idx, name=row["name"], vn_kv=row["vn_kv"],
                   in_service=row["in_service"])

    def _add_edges(table: pd.DataFrame, from_col: str, to_col: str) -> None:
        for idx, row in table.iterrows():
            if in_service_only and not row.get("in_service", True):
                continue
            G.add_edge(row[from_col], row[to_col], element_idx=idx)

    _add_edges(net.line,   "from_bus", "to_bus")
    _add_edges(net.trafo,  "hv_bus",   "lv_bus")
    _add_edges(net.trafo3w, "hv_bus",  "mv_bus")
    _add_edges(net.trafo3w, "hv_bus",  "lv_bus")
    return G


def find_islands(net: pp.pandapowerNet) -> list[set[int]]:
    """Return list of sets of bus indices, one set per connected island."""
    G = build_nx_graph(net, in_service_only=True)
    return [set(c) for c in nx.connected_components(G)]


def report_islands(net: pp.pandapowerNet) -> None:
    """Print each island with generation status."""
    islands = find_islands(net)

    # Buses with in-service generation
    powered = set()
    if not net.ext_grid.empty:
        powered |= set(
            net.ext_grid.loc[net.ext_grid["in_service"], "bus"].values)
    if not net.gen.empty:
        powered |= set(
            net.gen.loc[net.gen["in_service"], "bus"].values)
    if not net.sgen.empty:
        powered |= set(
            net.sgen.loc[net.sgen["in_service"], "bus"].values)

    print(f"\n{'='*60}")
    print(f"Islands found: {len(islands)}")
    print(f"{'='*60}")
    for i, island in enumerate(sorted(islands, key=len, reverse=True), 1):
        has_power = bool(island & powered)
        status = "OK" if has_power else "*** NO GENERATION ***"
        print(f"\nIsland {i:3d}  [{len(island):4d} buses]  {status}")
        if not has_power:
            for bus_idx in sorted(island):
                row = net.bus.loc[bus_idx]
                print(f"       bus {bus_idx:6d}  {row['name']:40s}  "
                      f"{row['vn_kv']:7.1f} kV")


def diagnose_bus(net: pp.pandapowerNet, bus_name: str) -> None:
    """Show all elements connected to a bus and their status."""
    matches = net.bus[net.bus["name"].str.strip() == bus_name.strip()]
    if matches.empty:
        print(f"Bus '{bus_name}' not found.")
        return

    bus_idx = matches.index[0]
    print(f"\nConnections for '{bus_name}' (bus_idx={bus_idx}):")

    for idx, row in net.line.iterrows():
        if row["from_bus"] == bus_idx or row["to_bus"] == bus_idx:
            other = row["to_bus"] if row["from_bus"] == bus_idx else row["from_bus"]
            print(f"  Line        {row['name']:40s} → "
                  f"{net.bus.at[other,'name']:30s}  in_service={row['in_service']}")

    for idx, row in net.trafo.iterrows():
        if row["hv_bus"] == bus_idx or row["lv_bus"] == bus_idx:
            other = row["lv_bus"] if row["hv_bus"] == bus_idx else row["hv_bus"]
            print(f"  Trafo       {row['name']:40s} → "
                  f"{net.bus.at[other,'name']:30s}  in_service={row['in_service']}")

    for idx, row in net.ext_grid.iterrows():
        if row["bus"] == bus_idx:
            print(f"  ExtGrid     {row['name']:40s}  in_service={row['in_service']}")

    for idx, row in net.gen.iterrows():
        if row["bus"] == bus_idx:
            print(f"  Gen (PV)    {row['name']:40s}  in_service={row['in_service']}")

    for idx, row in net.sgen.iterrows():
        if row["bus"] == bus_idx:
            print(f"  Sgen (PQ)   {row['name']:40s}  in_service={row['in_service']}")
