"""Which violations limited the installable capacity?"""
from __future__ import annotations
import math
import pandas as pd

COLUMNS = ["rank", "likely_bottleneck", "case_id", "kind", "element_type",
           "element_index", "element_name", "limit", "value_at_best",
           "value_at_limiting", "margin_at_best", "exceedance_at_limiting_percent",
           "estimated_critical_mw", "best_mw", "limiting_mw", "message"]


def _find_result(results, case_id):
    for result in results or []:
        if result.outage.case_id == case_id:
            return result
    return None


def value_in_results(results, violation):
    """The monitored quantity of ``violation`` inside another set of results."""
    result = _find_result(results, violation.case_id)
    if result is None or not result.converged:
        return None
    if violation.kind == "overload":
        table = {"line": result.post.line_loading_percent,
                 "trafo": result.post.trafo_loading_percent,
                 "trafo3w": result.post.trafo3w_loading_percent}.get(violation.element_type, {})
        return table.get(violation.element_index)
    if violation.kind in {"overvoltage", "undervoltage"}:
        return result.post.bus_vm_pu.get(violation.element_index)
    if violation.kind == "target_voltage_change":
        value = result.voltage_change_pu
        return None if math.isnan(value) else value
    return None


def _num(value):
    return None if value is None or (isinstance(value, float) and not math.isfinite(value)) else float(value)


def bottleneck_frame(search, name_lookup=None) -> pd.DataFrame:
    """Rank the significant violations at the smallest infeasible size.

    estimated_critical_mw interpolates linearly between best_mw and
    limiting_mw and is only given when the quantity was still within its
    limit at best_mw. The lowest estimate is the likely bottleneck.
    """
    rows = []
    for v in search.blocking_at_limit or []:
        limit, at_limit = _num(v.limit), _num(v.value)
        at_best = _num(value_in_results(search.results, v))
        under = v.kind == "undervoltage"
        margin = exceed = critical = None
        if limit not in (None, 0.0) and at_limit is not None:
            exceed_abs = (limit - at_limit) if under else (at_limit - limit)
            exceed = exceed_abs / abs(limit) * 100.0
            if at_best is not None:
                margin = (at_best - limit) if under else (limit - at_best)
                if margin > 0 and exceed_abs > 0 and search.limiting_mw is not None:
                    fraction = margin / (margin + exceed_abs)
                    critical = search.best_mw + fraction * (search.limiting_mw - search.best_mw)
        rows.append({
            "case_id": v.case_id, "kind": v.kind, "element_type": v.element_type,
            "element_index": v.element_index,
            "element_name": name_lookup(v.element_type, v.element_index) if name_lookup else None,
            "limit": limit, "value_at_best": at_best, "value_at_limiting": at_limit,
            "margin_at_best": margin, "exceedance_at_limiting_percent": exceed,
            "estimated_critical_mw": critical, "best_mw": search.best_mw,
            "limiting_mw": search.limiting_mw, "message": v.message,
        })
    frame = pd.DataFrame(rows, columns=[c for c in COLUMNS if c not in {"rank", "likely_bottleneck"}])
    if frame.empty:
        return pd.DataFrame(columns=COLUMNS)
    frame = frame.sort_values(["estimated_critical_mw", "exceedance_at_limiting_percent"],
                              ascending=[True, False], na_position="last").reset_index(drop=True)
    frame.insert(0, "rank", range(1, len(frame) + 1))
    frame.insert(1, "likely_bottleneck", frame["rank"].eq(1))
    return frame


def summary_text(search, frame) -> str:
    if search is None:
        return "No capacity search was needed (full requested capacity feasible)."
    if frame.empty:
        return f"No limiting violation: no infeasible size was found up to {search.original_mw:g} MW."
    top = frame.iloc[0]
    where = f"{top.element_type}[{top.element_index}]" + (f" {top.element_name}" if top.element_name else "")
    return (f"Best {search.best_mw:.1f} MW; infeasible at {search.limiting_mw:.1f} MW. "
            f"Likely bottleneck: {top.kind} on {where} in case {top.case_id} "
            f"({int(len(frame))} significant violation(s) at the limiting size).")
