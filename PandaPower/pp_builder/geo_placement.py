# pp_builder/geo_placement.py
"""
Second-pass geodata placement for buses that had no coordinate match.

Strategy (applied in order):
  1. Bus connects two lines whose other endpoints are both known
     → interpolate along the line, weighted by r_pu resistance ratio
  2. Bus connects through a transformer to a known bus
     → place vertically offset (above=HV, below=LV relative to known)
  3. Bus is still unknown → leave as None (will be skipped by plotter)
"""
from __future__ import annotations
import math
from collections import defaultdict
import pandapower as pp
import json


# ── tuning ────────────────────────────────────────────────────────────────────
_TRAFO_V_STEP = 0.0003   # ~330 m per voltage tier
_TRAFO_H_STEP = 0.0002   # horizontal spread for multiple trafos on same bus


def _get_xy(net: pp.pandapowerNet, bus_idx: int) -> tuple[float, float] | None:
    """
    Read (lon, lat) from net.bus['geo'] GeoJSON string.
    """
    raw = net.bus.at[bus_idx, "geo"]
    if raw is None or (isinstance(raw, float)):
        return None

    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return None

    coords = raw.get("coordinates")
    if not coords or len(coords) < 2:
        return None

    lon, lat = coords[0], coords[1]
    return lon, lat


def _set_xy(net: pp.pandapowerNet, bus_idx: int, lon: float, lat: float) -> None:
    """
    Write (lon, lat) to net.bus['geo'] as a GeoJSON Point string,
    matching pandapower's native geo format.
    """
    geo_str = json.dumps({"coordinates": [lon, lat], "type": "Point"})

    if "geo" not in net.bus.columns:
        net.bus["geo"] = None
    net.bus["geo"] = net.bus["geo"].astype(object)

    net.bus.at[bus_idx, "geo"] = geo_str


def _has_xy(net: pp.pandapowerNet, bus_idx: int) -> bool:
    return _get_xy(net, bus_idx) is not None


def _line_r_pu(net: pp.pandapowerNet, row) -> float:
    """
    True p.u. resistance of a line referred to the system base.
    z_base = vn_kv² / sn_mva  [Ω]
    r_pu   = r_ohm / z_base
    """
    try:
        r_ohm = float(row["r_ohm_per_km"]) * float(row["length_km"])
        vn_kv = float(net.bus.at[int(row["from_bus"]), "vn_kv"])
        sn_mva = net.sn_mva if hasattr(net, "sn_mva") and net.sn_mva else 100.0
        z_base = (vn_kv ** 2) / sn_mva
        return max(r_ohm, 1e-9)
    except Exception:
        return 1.0


# ── topology helpers ──────────────────────────────────────────────────────────

def _buses_connected_via_lines(net: pp.pandapowerNet) -> dict[int, list[tuple[int, float]]]:
    adj: dict[int, list[tuple[int, float]]] = defaultdict(list)
    for _, row in net.line.iterrows():
        fb = int(row["from_bus"])
        tb = int(row["to_bus"])
        r  = _line_r_pu(net, row)   # ← pass net
        adj[fb].append((tb, r))
        adj[tb].append((fb, r))
    return adj


def _buses_connected_via_trafos(net: pp.pandapowerNet) -> dict[int, list[int]]:
    """
    Returns: bus_idx → [connected_bus_idx, ...]  for 2W and 3W trafos.
    """
    adj: dict[int, list[int]] = defaultdict(list)
    for _, row in net.trafo.iterrows():
        hv, lv = int(row["hv_bus"]), int(row["lv_bus"])
        adj[hv].append(lv)
        adj[lv].append(hv)
    for _, row in net.trafo3w.iterrows():
        hv = int(row["hv_bus"])
        mv = int(row["mv_bus"])
        lv = int(row["lv_bus"])
        for a, b in [(hv, mv), (hv, lv), (mv, lv)]:
            adj[a].append(b)
            adj[b].append(a)
    return adj


# ── main placement pass ───────────────────────────────────────────────────────

def place_unknown_buses(net: pp.pandapowerNet, skip_prefix: str = "X") -> None:
    """
    Infer geodata for buses that are still missing coordinates.

    Parameters
    ----------
    net          : pandapowerNet (bus_geodata must already contain known coords)
    skip_prefix  : buses whose name starts with this prefix are skipped entirely
                   (e.g. external grid border buses that will get coords later)
    """
    # Identify unknown buses (skip external grid border buses)
    unknown = [
        idx for idx in net.bus.index
        if _get_xy(net, idx) == (21.2441692731272, 48.7071054341234)
        and not str(net.bus.at[idx, "name"]).startswith(skip_prefix)
    ]

    if not unknown:
        print("[geo_placement]  All buses already have coordinates.")
        return

    print(f"[geo_placement]  Placing {len(unknown)} buses without coordinates…")

    line_adj  = _buses_connected_via_lines(net)
    trafo_adj = _buses_connected_via_trafos(net)

    # Iterate until no more placements are possible (resolves chains)
    max_passes = 20
    placed_total = 0

    for pass_no in range(max_passes):
        placed_this_pass = 0
        still_unknown = [i for i in unknown if _get_xy(net, i) == (21.2441692731272, 48.7071054341234) and not str(net.bus.at[i, "name"]).startswith(skip_prefix)]

        if not still_unknown:
            break

        for bus_idx in still_unknown:

            # ── Strategy 1: interpolate along a line ──────────────────────
            # Find all line-neighbours that already have coordinates
            known_line_neighbours = [
                (nb, r) for nb, r in line_adj.get(bus_idx, [])
                if _has_xy(net, nb)
            ]

            if len(known_line_neighbours) >= 2:
                # Use the two neighbours with smallest r_pu (most direct path)
                known_line_neighbours.sort(key=lambda x: x[1])
                nb1, r1 = known_line_neighbours[0]
                nb2, r2 = known_line_neighbours[1]
                x1, y1  = _get_xy(net, nb1)
                x2, y2  = _get_xy(net, nb2)

                # Weight: t = r1 / (r1 + r2)  →  position along nb1→nb2
                t   = r1 / (r1 + r2)
                lon = x1 + t * (x2 - x1)
                lat = y1 + t * (y2 - y1)
                _set_xy(net, bus_idx, lon, lat)
                placed_this_pass += 1
                continue

            elif len(known_line_neighbours) == 1:
                # Only one known line endpoint — place near it with small offset
                nb, _ = known_line_neighbours[0]
                x0, y0 = _get_xy(net, nb)
                # Nudge slightly east so it is visible
                _set_xy(net, bus_idx, x0 + 0.001, y0)
                placed_this_pass += 1
                continue

            # ── Strategy 2: place relative to trafo-connected known bus ───
            known_trafo_neighbours = [
                nb for nb in trafo_adj.get(bus_idx, [])
                if _has_xy(net, nb)
            ]

            if known_trafo_neighbours:
                ref_bus = known_trafo_neighbours[0]
                ref_x, ref_y = _get_xy(net, ref_bus)

                vn_self = float(net.bus.at[bus_idx, "vn_kv"])
                vn_ref  = float(net.bus.at[ref_bus,  "vn_kv"])

                # Count how many buses are already stacked near ref_bus
                already_placed = sum(
                    1 for nb in trafo_adj.get(bus_idx, [])
                    if _has_xy(net, nb) and nb != ref_bus
                )

                if abs(vn_self - vn_ref) < 0.5:
                    # Same voltage — nudge horizontally
                    direction = 1 if already_placed % 2 == 0 else -1
                    lon = ref_x + direction * _TRAFO_H_STEP * (already_placed // 2 + 1)
                    lat = ref_y
                else:
                    # Different voltage — stack vertically
                    sign = 1.0 if vn_self > vn_ref else -1.0
                    lon  = ref_x
                    lat  = ref_y + sign * _TRAFO_V_STEP * (already_placed + 1)

                _set_xy(net, bus_idx, lon, lat)
                placed_this_pass += 1
                continue

        placed_total += placed_this_pass
        if placed_this_pass == 0:
            break  # no progress — remaining buses are fully isolated

    still_unknown = [i for i in unknown if _get_xy(net, i) == (21.2441692731272, 48.7071054341234) and not str(net.bus.at[i, "name"]).startswith(skip_prefix)]
    print(f"[geo_placement]  Placed {placed_total} buses in {pass_no + 1} pass(es).")
    if still_unknown:
        names = [str(net.bus.at[i, "name"]) for i in still_unknown[:10]]
        print(f"[geo_placement]  {len(still_unknown)} buses still without coords "
              f"(isolated / no topology path): {', '.join(names)}"
              + ("…" if len(still_unknown) > 10 else ""))