"""
network_visualiser.py — NetworkX + Matplotlib diagram of a pandapower network.
Voltage-tier layout, seasonal colour coding.  No IPSA tools required.
"""
from __future__ import annotations

import collections
import pandapower as pp
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import matplotlib.lines as mlines
import networkx as nx

from .network_topology import build_nx_graph


VOLTAGE_TIERS = [
    (380, 999, 4.0, "#d62728", "400 kV"),
    (200, 379, 3.0, "#ff7f0e", "220 kV"),
    (110, 199, 2.0, "#2ca02c", "132 kV"),
    ( 30, 109, 1.0, "#1f77b4", "MV"),
    (  0,  29, 0.0, "#9467bd", "LV"),
]


def _tier(kv: float):
    for lo, hi, y, colour, label in VOLTAGE_TIERS:
        if lo <= kv <= hi:
            return y, colour, label
    return 0.0, "#7f7f7f", "Unknown"


def _tiered_layout(net: pp.pandapowerNet, G: nx.Graph) -> dict:
    tiers = collections.defaultdict(list)
    for node in G.nodes:
        kv = net.bus.at[node, "vn_kv"]
        y, *_ = _tier(kv)
        tiers[y].append(node)

    pos = {}
    for y_val, nodes in tiers.items():
        sub = G.subgraph(nodes)
        if sub.number_of_edges() > 0:
            sub_pos = nx.kamada_kawai_layout(sub)
        else:
            sub_pos = {nd: (i / max(len(nodes)-1, 1), 0.0)
                       for i, nd in enumerate(nodes)}
        xs = [v[0] for v in sub_pos.values()]
        x_range = (max(xs) - min(xs)) or 1.0
        for nd, (x, _) in sub_pos.items():
            pos[nd] = ((x - min(xs)) / x_range, y_val)
    return pos


def draw_network(
    net: pp.pandapowerNet,
    output_path: str = "network.png",
    figsize: tuple = (22, 14),
    show_labels: bool = True,
    label_fontsize: int = 6,
    dpi: int = 150,
) -> None:
    G   = build_nx_graph(net, in_service_only=False)
    pos = _tiered_layout(net, G)

    fig, ax = plt.subplots(figsize=figsize)
    ax.set_facecolor("#1a1a2e")
    fig.patch.set_facecolor("#1a1a2e")

    line_set   = set(zip(net.line["from_bus"], net.line["to_bus"]))
    trafo_set  = set(zip(net.trafo["hv_bus"],  net.trafo["lv_bus"]))
    trafo3_set = (set(zip(net.trafo3w["hv_bus"], net.trafo3w["mv_bus"])) |
                  set(zip(net.trafo3w["hv_bus"], net.trafo3w["lv_bus"])))

    edge_lines_in, edge_lines_out, edge_trafo = [], [], []
    for u, v, d in G.edges(data=True):
        pair = (u, v)
        rpair = (v, u)
        if pair in line_set or rpair in line_set:
            # check in_service from net.line
            mask = ((net.line["from_bus"] == u) & (net.line["to_bus"] == v)) | \
                   ((net.line["from_bus"] == v) & (net.line["to_bus"] == u))
            in_svc = net.line.loc[mask, "in_service"].all()
            (edge_lines_in if in_svc else edge_lines_out).append((u, v))
        else:
            edge_trafo.append((u, v))

    kw = dict(G=G, pos=pos, ax=ax)
    nx.draw_networkx_edges(**kw, edgelist=edge_lines_in,
                           edge_color="#aec6cf", width=1.2, alpha=0.85)
    nx.draw_networkx_edges(**kw, edgelist=edge_lines_out,
                           edge_color="#555555", width=0.8,
                           alpha=0.5, style="dashed")
    nx.draw_networkx_edges(**kw, edgelist=edge_trafo,
                           edge_color="#f4a460", width=2.0, alpha=0.9)

    colour_groups = collections.defaultdict(list)
    for node in G.nodes:
        kv = net.bus.at[node, "vn_kv"]
        _, colour, _ = _tier(kv)
        colour_groups[colour].append(node)
    for colour, nodes in colour_groups.items():
        nx.draw_networkx_nodes(**kw, nodelist=nodes,
                               node_color=colour, node_size=60, alpha=0.95)

    if show_labels:
        labels = {n: str(net.bus.at[n, "name"])[:16] for n in G.nodes}
        nx.draw_networkx_labels(**kw, labels=labels,
                                font_size=label_fontsize, font_color="white")

    patches = [mpatches.Patch(color=c, label=l)
               for _, _, _, c, l in VOLTAGE_TIERS]
    patches += [
        mlines.Line2D([], [], color="#aec6cf", lw=1.5, label="Line (in service)"),
        mlines.Line2D([], [], color="#555555", lw=1.0, linestyle="dashed",
                      label="Line (out of service)"),
        mlines.Line2D([], [], color="#f4a460", lw=2.5, label="Transformer"),
    ]
    ax.legend(handles=patches, loc="upper left", fontsize=8,
              facecolor="#2a2a4a", labelcolor="white", framealpha=0.8)

    stats = (f"Buses: {len(net.bus)}   Lines: {len(net.line)}   "
             f"Trafos: {len(net.trafo)+len(net.trafo3w)}")
    ax.text(0.01, 0.01, stats, transform=ax.transAxes,
            fontsize=8, color="white", alpha=0.7, va="bottom")
    ax.set_title("pandapower Network Diagram", color="white", fontsize=14, pad=12)
    ax.axis("off")
    plt.tight_layout()
    plt.savefig(output_path, dpi=dpi, bbox_inches="tight",
                facecolor=fig.get_facecolor())
    plt.close()
    print(f"[visualiser]  Saved → {output_path}")
