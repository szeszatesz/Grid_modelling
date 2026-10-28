from __future__ import annotations
from typing import Hashable
import networkx as nx
from .selectors import resolve_one

def build_graph(net, respect_switches: bool = True) -> nx.MultiGraph:
    """Build a MultiGraph; unlike a simple graph, parallel circuits are retained."""
    graph = nx.MultiGraph()
    for idx, row in net.bus.iterrows():
        if bool(row.get("in_service", True)):
            graph.add_node(idx, name=row.get("name"), kv=float(row.get("vn_kv", 0.0)))

    open_ends = set()
    if respect_switches and "switch" in net:
        for _, sw in net.switch.iterrows():
            if bool(sw.get("in_service", True)) and not bool(sw.closed):
                open_ends.add((str(sw.et), sw.element, sw.bus))

    def add(table, idx, a, b):
        row = net[table].loc[idx]
        if not bool(row.get("in_service", True)) or a not in graph or b not in graph:
            return
        et = {"line": "l", "trafo": "t"}.get(table)
        if et and ((et, idx, a) in open_ends or (et, idx, b) in open_ends):
            return
        graph.add_edge(a, b, key=(table, idx), element_type=table, index=idx,
                       name=row.get("name"), in_service=True)

    for idx, row in net.line.iterrows():
        add("line", idx, row.from_bus, row.to_bus)
    for idx, row in net.trafo.iterrows():
        add("trafo", idx, row.hv_bus, row.lv_bus)
    for idx, row in net.trafo3w.iterrows():
        if not bool(row.get("in_service", True)):
            continue
        star = ("trafo3w", idx)
        graph.add_node(star, name=row.get("name"), kv=float(row.get("vn_hv_kv", 0.0)), virtual=True)
        for side in ("hv", "mv", "lv"):
            bus = row[f"{side}_bus"]
            if bus in graph:
                graph.add_edge(bus, star, key=("trafo3w", idx, side),
                               element_type="trafo3w", index=idx, name=row.get("name"))
    return graph

def get_relevant_contingency_lines(net, target_bus, ignored=None,
                                   hv_threshold_kv: float = 130.0,
                                   lv_threshold_kv: float = 100.0) -> list[Hashable]:
    """IPSA-equivalent voltage-layer selection, retaining parallel lines."""
    target = resolve_one(net, "bus", target_bus)
    ignored = set(ignored or ())
    graph = build_graph(net)
    target_kv = float(graph.nodes[target]["kv"])
    relevant, feeding_hv, visited = set(), set(), set()

    def line_edges(node):
        for _, nbr, key, data in graph.edges(node, keys=True, data=True):
            if data.get("element_type") == "line":
                yield nbr, data["index"]

    if target_kv < hv_threshold_kv:
        queue = [target]
        while queue:
            node = queue.pop()
            if node in visited:
                continue
            visited.add(node)
            for nbr, idx in line_edges(node):
                if idx in ignored:
                    continue
                nbr_kv = float(graph.nodes[nbr]["kv"])
                if nbr_kv >= hv_threshold_kv:
                    feeding_hv.add(nbr)
                    relevant.add(idx)
                elif nbr_kv >= lv_threshold_kv:
                    relevant.add(idx)
                    if nbr not in visited:
                        queue.append(nbr)
        for node in feeding_hv:
            for nbr, idx in line_edges(node):
                if idx not in ignored and float(graph.nodes[nbr]["kv"]) >= hv_threshold_kv:
                    relevant.add(idx)
    else:
        for nbr, idx in line_edges(target):
            if idx not in ignored and float(graph.nodes[nbr]["kv"]) >= hv_threshold_kv:
                relevant.add(idx)
    return sorted(relevant, key=str)
