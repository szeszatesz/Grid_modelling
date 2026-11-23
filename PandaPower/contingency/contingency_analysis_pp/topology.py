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


# ---------------------------------------------------------------------------
# Extended N-1 scope: neighbouring 120 kV lines, substation transformers and
# the HV (220/400 kV) lines of the closing substations.
# ---------------------------------------------------------------------------
from collections import deque
from dataclasses import dataclass, field
import pandas as pd
from .models import Outage

SCOPE_COLUMNS = ["element_type", "element_index", "element_name", "from_bus", "to_bus",
                 "reason", "substation_bus", "substation_class", "depth", "path"]


@dataclass
class ContingencyScope:
    """Elements selected for N-1 testing, with the reason for each one."""
    target: Hashable
    table: pd.DataFrame
    notes: list[str] = field(default_factory=list)

    def indices(self, element_type: str) -> list:
        frame = self.table
        return frame.loc[frame.element_type.eq(element_type), "element_index"].tolist()

    def outages(self) -> list[Outage]:
        return [Outage(str(r.element_type), r.element_index,
                       None if pd.isna(r.element_name) else str(r.element_name))
                for r in self.table.itertuples()]


def _bus_name(net, bus):
    try:
        return net.bus.at[bus, "name"]
    except (KeyError, AttributeError):
        return bus


def get_contingency_scope(net, target_bus, *, ignored=None,
                          mode: str = "extended",
                          dso_min_kv: float = 100.0,
                          tso_min_kv: float = 200.0,
                          max_other_lines: int = 3,
                          max_depth: int | None = None,
                          include_target_equipment: bool = False) -> ContingencyScope:
    """Select the N-1 cases around a target busbar.

    mode='target_lines': only the lines connected to the target busbar.
    mode='extended':
      * all lines of the target busbar are tested, then the search follows the
        lines of the dso_min_kv..tso_min_kv layer outwards;
      * at every busbar reached, "other lines" are the layer lines that do not
        lead back to the bus the search came from. With 1..max_other_lines the
        search continues along ALL of them; with 0 it simply ends;
      * the path is CLOSED at a TSO feed substation (the busbar has a
        transformer, 2- or 3-winding, to a bus >= tso_min_kv) and at a main DSO
        substation (more than max_other_lines other lines). Their arriving
        line is tested, the lines leaving them are not followed;
      * in every closing substation ALL transformers are tested, and so are
        the HV lines (both ends >= tso_min_kv) of the HV busbars those
        transformers feed from. The search does not continue beyond them.
    ``ignored`` holds ElementRef or raw line indices to skip.
    If the target is outside the dso layer the legacy voltage-layer selection
    is used (lines only).
    """
    if mode not in {"extended", "target_lines"}:
        raise ValueError("mode must be 'extended' or 'target_lines'")
    target = resolve_one(net, "bus", target_bus)
    ignored_refs = set(ignored or ())
    ignored_ids = {(getattr(r, "element_type", "line"), getattr(r, "index", r))
                   for r in ignored_refs}
    graph = build_graph(net)
    kv = lambda bus: float(graph.nodes[bus]["kv"])
    notes: list[str] = []
    found: dict[tuple, dict] = {}

    def skip(table, idx):
        return (table, idx) in ignored_ids

    def add(table, idx, reason, depth, sub_bus=None, sub_class=None, path=None):
        if skip(table, idx) or (table, idx) in found:
            return
        row = net[table].loc[idx]
        if table == "line":
            a, b = row.from_bus, row.to_bus
        elif table == "trafo":
            a, b = row.hv_bus, row.lv_bus
        else:
            a, b = row.hv_bus, row.lv_bus
        found[(table, idx)] = {
            "element_type": table, "element_index": idx,
            "element_name": row.get("name"),
            "from_bus": _bus_name(net, a), "to_bus": _bus_name(net, b),
            "reason": reason,
            "substation_bus": None if sub_bus is None else _bus_name(net, sub_bus),
            "substation_class": sub_class, "depth": depth,
            "path": None if path is None else " > ".join(str(_bus_name(net, p)) for p in path),
        }

    def layer_lines(bus, layer_only=True):
        out = []
        for _, nbr, _key, data in graph.edges(bus, keys=True, data=True):
            if data.get("element_type") != "line" or nbr == bus:
                continue
            if layer_only and not (dso_min_kv <= kv(nbr) < tso_min_kv):
                continue
            out.append((nbr, data["index"]))
        return sorted(out, key=lambda x: str(x[1]))

    def transformer_terminals(bus):
        """[(table, idx, [other buses])] of in-service transformers at bus."""
        seen, out = set(), []
        for _, nbr, _key, data in graph.edges(bus, keys=True, data=True):
            table = data.get("element_type")
            if table not in {"trafo", "trafo3w"}:
                continue
            idx = data["index"]
            if (table, idx) in seen:
                continue
            seen.add((table, idx))
            if table == "trafo":
                others = [nbr]
            else:
                row = net.trafo3w.loc[idx]
                others = [row[f"{s}_bus"] for s in ("hv", "mv", "lv")
                          if row[f"{s}_bus"] != bus and row[f"{s}_bus"] in graph]
            out.append((table, idx, others))
        return sorted(out, key=lambda x: (x[0], str(x[1])))

    def is_tso_feed(bus):
        return any(kv(o) >= tso_min_kv
                   for _, _, others in transformer_terminals(bus) for o in others)

    def add_substation_equipment(bus, sub_class, depth, path):
        for table, idx, others in transformer_terminals(bus):
            add(table, idx, f"transformer in {sub_class}", depth, bus, sub_class, path)
            for hv_bus in others:
                if kv(hv_bus) < tso_min_kv:
                    continue
                for _, nbr, _key, data in graph.edges(hv_bus, keys=True, data=True):
                    if (data.get("element_type") == "line" and nbr != hv_bus
                            and kv(nbr) >= tso_min_kv):
                        add("line", data["index"], f"HV line leaving {sub_class}",
                            depth, bus, sub_class, path)

    target_kv = kv(target)
    if not (dso_min_kv <= target_kv < tso_min_kv) and mode == "extended":
        notes.append(f"Target busbar is {target_kv:g} kV, outside the {dso_min_kv:g}-{tso_min_kv:g} kV "
                     "layer: legacy voltage-layer line selection used, no transformer cases.")
        for idx in get_relevant_contingency_lines(net, target, ignored={i for t, i in ignored_ids if t == "line"}):
            add("line", idx, "legacy voltage-layer selection", 1)
    elif mode == "target_lines":
        for _, idx in layer_lines(target, layer_only=False):
            add("line", idx, "line at target busbar", 1, path=[target])
    else:
        closed: dict = {}
        visited = {target}
        queue = deque([(target, None, 0, [target])])
        while queue:
            bus, arrival, depth, path = queue.popleft()
            others = [(n, i) for n, i in layer_lines(bus) if n != arrival]
            if bus != target:
                if is_tso_feed(bus):
                    closed[bus] = ("TSO feed substation", depth, path)
                    continue
                if len(others) > max_other_lines:
                    closed[bus] = ("main DSO substation", depth, path)
                    continue
            if max_depth is not None and depth >= max_depth:
                continue
            for nbr, idx in others:
                add("line", idx, "line at target busbar" if bus == target else
                    f"line on search path ({len(others)} other line(s) at {_bus_name(net, bus)})",
                    depth + 1, path=path + [nbr])
                if nbr not in visited:
                    visited.add(nbr)
                    queue.append((nbr, bus, depth + 1, path + [nbr]))
        for bus, (sub_class, depth, path) in closed.items():
            add_substation_equipment(bus, sub_class, depth, path)
        if include_target_equipment:
            cls = "TSO feed substation" if is_tso_feed(target) else "target busbar"
            add_substation_equipment(target, cls, 0, [target])

    table = pd.DataFrame(list(found.values()), columns=SCOPE_COLUMNS)
    if not table.empty:
        order = {"line": 0, "trafo": 1, "trafo3w": 2}
        table = table.assign(_o=table.element_type.map(order))
        try:
            table = table.sort_values(["_o", "depth", "element_index"])
        except TypeError:
            table = table.sort_values(["_o", "depth"], kind="stable")
        table = table.drop(columns="_o").reset_index(drop=True)
    return ContingencyScope(target, table, notes)
