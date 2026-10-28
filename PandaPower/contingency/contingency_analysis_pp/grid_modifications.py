from __future__ import annotations
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Hashable
import json
import math
import numpy as np
import pandas as pd
import pandapower as pp
import pandapower.toolbox as tb

from .selectors import Selector, resolve_one, select_elements

class GridTransaction:
    """Deep-copy transaction for arbitrary edits; rollback unless committed."""
    def __init__(self, net):
        self.net = net
        self._backup = None
        self._committed = False

    def __enter__(self):
        self._backup = deepcopy(self.net)
        return self

    def commit(self) -> None:
        self._committed = True

    def rollback(self) -> None:
        if self._backup is not None:
            self.net.clear()
            self.net.update(deepcopy(self._backup))

    def __exit__(self, exc_type, exc, tb_):
        if exc_type is not None or not self._committed:
            self.rollback()
        return False

@contextmanager
def temporary_grid(net):
    """Apply any edits inside the block and restore the exact input net afterwards."""
    with GridTransaction(net):
        yield net

def modify_elements(net, element_type: str, selector: Selector, /,
                    allow_new_columns: bool = False, **parameters: Any) -> list[Hashable]:
    """Modify arbitrary columns on selected elements and return their indices."""
    indices = select_elements(net, element_type, selector)
    table = net[element_type]
    missing = [column for column in parameters if column not in table.columns]
    if missing and not allow_new_columns:
        raise KeyError(f"Unknown columns in net.{element_type}: {missing}")
    for column, value in parameters.items():
        if column not in table.columns:
            table[column] = np.nan
        if callable(value):
            for idx in indices:
                table.at[idx, column] = value(table.loc[idx])
        else:
            table.loc[indices, column] = value
    return indices

def set_elements_in_service(net, element_type: str, selector: Selector, in_service: bool) -> list[Hashable]:
    table = net[element_type]
    if "in_service" not in table.columns:
        raise ValueError(f"net.{element_type} has no 'in_service' state")
    return modify_elements(net, element_type, selector, in_service=bool(in_service))

def switch_on(net, element_type: str, selector: Selector) -> list[Hashable]:
    """Energize an element; physical switches are closed, others put in service."""
    if element_type == "switch":
        return set_switches_closed(net, selector, True)
    return set_elements_in_service(net, element_type, selector, True)

def switch_off(net, element_type: str, selector: Selector) -> list[Hashable]:
    """De-energize an element; physical switches are opened, others taken out of service."""
    if element_type == "switch":
        return set_switches_closed(net, selector, False)
    return set_elements_in_service(net, element_type, selector, False)

def set_switches_closed(net, selector: Selector, closed: bool) -> list[Hashable]:
    """Open/close actual rows in net.switch (different from element service state)."""
    return modify_elements(net, "switch", selector, closed=bool(closed))

def delete_busbars(net, selector: Selector) -> list[Hashable]:
    """Delete selected buses and all connected elements, switches and geodata."""
    buses = select_elements(net, "bus", selector)
    if buses:
        tb.drop_buses(net, buses, drop_elements=True)
    return buses

def delete_elements(net, element_type: str, selector: Selector,
                    cascade_bus: bool = True) -> list[Hashable]:
    """Delete selected rows. Bus deletion is cascading by default."""
    indices = select_elements(net, element_type, selector)
    if not indices:
        return []
    if element_type == "bus" and cascade_bus:
        return delete_busbars(net, indices)
    if hasattr(tb, "drop_elements"):
        try:
            tb.drop_elements(net, element_type, indices)
            return indices
        except (TypeError, KeyError, NotImplementedError):
            pass
    net[element_type].drop(index=indices, inplace=True)
    result_table = f"res_{element_type}"
    if result_table in net and isinstance(net[result_table], pd.DataFrame):
        net[result_table].drop(index=indices, errors="ignore", inplace=True)
    if element_type == "line":
        if "line_geodata" in net and isinstance(net.line_geodata, pd.DataFrame):
            net.line_geodata.drop(index=indices, errors="ignore", inplace=True)
        if "switch" in net and not net.switch.empty:
            sw = net.switch.index[(net.switch.et == "l") & net.switch.element.isin(indices)]
            net.switch.drop(index=sw, inplace=True)
    return indices

def _new_index(df: pd.DataFrame) -> int:
    return 0 if df.empty else int(max(df.index)) + 1

def _copy_extra_line_columns(net, source: pd.Series, target_indices: list[Hashable]) -> None:
    protected = {"name", "from_bus", "to_bus", "length_km"}
    for column in net.line.columns:
        if column in protected or column not in source.index:
            continue
        value = source[column]
        for idx in target_indices:
            net.line.at[idx, column] = deepcopy(value)

def _decode_coords(value):
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return None
    return deepcopy(value) if isinstance(value, (list, tuple)) else None

def _polyline_split(coords, fraction: float):
    if not coords or len(coords) < 2:
        return None, None, None
    points = [np.asarray(p, dtype=float) for p in coords]
    lengths = [float(np.linalg.norm(b - a)) for a, b in zip(points[:-1], points[1:])]
    total = sum(lengths)
    if total <= 0:
        split = points[0]
        return [p.tolist() for p in points], [p.tolist() for p in points], split.tolist()
    target, walked = fraction * total, 0.0
    for i, seglen in enumerate(lengths):
        if walked + seglen >= target:
            local = 0.0 if seglen == 0 else (target - walked) / seglen
            split = points[i] + local * (points[i + 1] - points[i])
            left = points[:i + 1] + [split]
            right = [split] + points[i + 1:]
            return [p.tolist() for p in left], [p.tolist() for p in right], split.tolist()
        walked += seglen
    return [p.tolist() for p in points], [points[-1].tolist()], points[-1].tolist()

def _line_coords(net, line_idx):
    if "line_geodata" in net and isinstance(net.line_geodata, pd.DataFrame) \
            and line_idx in net.line_geodata.index and "coords" in net.line_geodata.columns:
        return _decode_coords(net.line_geodata.at[line_idx, "coords"]), "line_geodata"
    if "geo" in net.line.columns:
        value = net.line.at[line_idx, "geo"]
        if isinstance(value, str):
            try:
                geo = json.loads(value)
                if geo and geo.get("type") == "LineString":
                    return geo.get("coordinates"), "geo"
            except (json.JSONDecodeError, AttributeError):
                pass
    return None, None

def add_busbar_to_line(net, line_selector: Selector, position: float, *,
                       position_unit: str = "fraction",
                       name: str | None = None, bus_kwargs: dict[str, Any] | None = None,
                       first_line_name: str | None = None,
                       second_line_name: str | None = None) -> tuple[Hashable, Hashable, Hashable]:
    """Insert a bus into one line at ``position`` measured from ``from_bus``.

    ``position_unit`` is ``"fraction"`` (0..1) or ``"km"`` (0..line length).

    The original line is replaced by two series segments. Length, impedance,
    shunt admittance and thermal rating remain physically equivalent. Existing
    terminal line switches are reassigned to the corresponding new segment.
    Returns ``(new_bus, first_line, second_line)``.
    """
    old_idx = resolve_one(net, "line", line_selector)
    old = net.line.loc[old_idx].copy()
    length = float(old.get("length_km", 1.0))
    if position_unit == "km":
        position = position / length
    elif position_unit != "fraction":
        raise ValueError("position_unit must be 'fraction' or 'km'")
    if not 0.0 < position < 1.0:
        raise ValueError("split point must lie strictly inside the line")
    from_bus, to_bus = old["from_bus"], old["to_bus"]
    vn_from, vn_to = float(net.bus.at[from_bus, "vn_kv"]), float(net.bus.at[to_bus, "vn_kv"])
    if not math.isclose(vn_from, vn_to, rel_tol=0.0, abs_tol=1e-6):
        raise ValueError(f"Cannot split line {old_idx}: terminal voltages differ ({vn_from}, {vn_to} kV)")

    kwargs = dict(bus_kwargs or {})
    kwargs.setdefault("vn_kv", vn_from)
    kwargs.setdefault("name", name or f"{old.get('name', old_idx)} @ {position:.1%}")
    kwargs.setdefault("in_service", bool(old.get("in_service", True)))
    new_bus = pp.create_bus(net, **kwargs)

    common = dict(
        r_ohm_per_km=float(old["r_ohm_per_km"]),
        x_ohm_per_km=float(old["x_ohm_per_km"]),
        c_nf_per_km=float(old.get("c_nf_per_km", 0.0)),
        g_us_per_km=float(old.get("g_us_per_km", 0.0)),
        max_i_ka=float(old["max_i_ka"]),
        df=float(old.get("df", 1.0)),
        parallel=int(old.get("parallel", 1)),
        in_service=bool(old.get("in_service", True)),
    )
    base_name = str(old.get("name", old_idx))
    first = pp.create_line_from_parameters(
        net, from_bus=from_bus, to_bus=new_bus, length_km=length * position,
        name=first_line_name or f"{base_name} [1]", **common)
    second = pp.create_line_from_parameters(
        net, from_bus=new_bus, to_bus=to_bus, length_km=length * (1.0 - position),
        name=second_line_name or f"{base_name} [2]", **common)
    _copy_extra_line_columns(net, old, [first, second])
    if "parent_line" not in net.line.columns:
        net.line["parent_line"] = None
    net.line.at[first, "parent_line"] = old_idx
    net.line.at[second, "parent_line"] = old_idx

    if "switch" in net and not net.switch.empty:
        mask = (net.switch.et == "l") & (net.switch.element == old_idx)
        for sw_idx, sw in net.switch.loc[mask].iterrows():
            if sw.bus == from_bus:
                net.switch.at[sw_idx, "element"] = first
            elif sw.bus == to_bus:
                net.switch.at[sw_idx, "element"] = second
            else:
                raise ValueError(f"Line switch {sw_idx} is not on either terminal of line {old_idx}")

    coords, storage = _line_coords(net, old_idx)
    left, right, split_point = _polyline_split(coords, position)
    if left is not None:
        if storage == "line_geodata":
            if "coords" not in net.line_geodata.columns:
                net.line_geodata["coords"] = None
            net.line_geodata.at[first, "coords"] = json.dumps(left)
            net.line_geodata.at[second, "coords"] = json.dumps(right)
        elif storage == "geo":
            net.line.at[first, "geo"] = json.dumps({"type": "LineString", "coordinates": left})
            net.line.at[second, "geo"] = json.dumps({"type": "LineString", "coordinates": right})
        if split_point is not None and "bus_geodata" in net and isinstance(net.bus_geodata, pd.DataFrame):
            net.bus_geodata.loc[new_bus, ["x", "y"]] = split_point[:2]

    net.line.drop(index=old_idx, inplace=True)
    if "res_line" in net:
        net.res_line.drop(index=old_idx, errors="ignore", inplace=True)
    if "line_geodata" in net and isinstance(net.line_geodata, pd.DataFrame):
        net.line_geodata.drop(index=old_idx, errors="ignore", inplace=True)
    return new_bus, first, second
