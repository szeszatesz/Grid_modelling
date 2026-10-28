from __future__ import annotations
from copy import deepcopy
from typing import Iterable
import math
import pandapower as pp

from .models import NetworkSnapshot, Outage, ContingencyResult
from .selectors import Selector, select_elements

def _float_dict(frame, column: str) -> dict:
    if frame is None or frame.empty or column not in frame.columns:
        return {}
    return {idx: float(value) for idx, value in frame[column].items()
            if value is not None and not math.isnan(float(value))}

def _snapshot(net, converged: bool) -> NetworkSnapshot:
    if not converged:
        return NetworkSnapshot(False)
    return NetworkSnapshot(
        converged=True,
        bus_vm_pu=_float_dict(net.get("res_bus"), "vm_pu"),
        line_loading_percent=_float_dict(net.get("res_line"), "loading_percent"),
        trafo_loading_percent=_float_dict(net.get("res_trafo"), "loading_percent"),
        trafo3w_loading_percent=_float_dict(net.get("res_trafo3w"), "loading_percent"),
    )

def _runpp(net, options: dict) -> tuple[bool, str | None]:
    try:
        pp.runpp(net, **options)
        return bool(net.get("converged", False)), None
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"

def make_outages(net, element_type: str, selector: Selector = None) -> list[Outage]:
    outages = []
    for idx in select_elements(net, element_type, selector):
        name = net[element_type].at[idx, "name"] if "name" in net[element_type] else None
        outages.append(Outage(element_type, idx, None if name is None else str(name)))
    return outages

def _state_column(table: str) -> str:
    return "closed" if table == "switch" else "in_service"

def run_contingencies(net, outages: Iterable[Outage], *, target_bus=None,
                      power_flow_options: dict | None = None,
                      restore_base_results: bool = True,
                      skip_already_off: bool = True) -> list[ContingencyResult]:
    """Run explicit per-case N-1 studies and retain complete case results.

    Pandapower's built-in aggregate contingency function is fast, but this
    explicit loop keeps the IPSA workflow's per-outage snapshots and errors.
    Element states are restored in ``finally`` blocks.
    """
    options = dict(power_flow_options or {})
    base_ok, base_error = _runpp(net, options)
    if not base_ok:
        raise RuntimeError(f"Base-case power flow did not converge: {base_error}")
    base = deepcopy(_snapshot(net, True))
    outages = list(outages)
    results: list[ContingencyResult] = []

    for outage in outages:
        if outage.element_type not in net or outage.index not in net[outage.element_type].index:
            raise KeyError(f"Missing outage element {outage.element_type}[{outage.index!r}]")
        column = _state_column(outage.element_type)
        if column not in net[outage.element_type].columns:
            raise ValueError(f"net.{outage.element_type} has no {column!r} state")
        old_state = bool(net[outage.element_type].at[outage.index, column])
        if skip_already_off and not old_state:
            continue
        try:
            net[outage.element_type].at[outage.index, column] = False
            converged, error = _runpp(net, options)
            post = deepcopy(_snapshot(net, converged))
            results.append(ContingencyResult(
                outage=outage, converged=converged, base=base, post=post,
                target_bus=target_bus, error=error,
            ))
        finally:
            net[outage.element_type].at[outage.index, column] = old_state

    if restore_base_results:
        ok, error = _runpp(net, options)
        if not ok:
            raise RuntimeError(f"Failed to restore base-case results: {error}")
    return results
