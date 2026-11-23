from __future__ import annotations
from dataclasses import dataclass, field
from typing import Callable, Iterable, Mapping, Hashable
import pandapower as pp

from .models import Outage, ContingencyResult, ElementRef
from .engine import run_contingencies
from .violations import Violation, check_violations, significant_only
from .scaling import seasonal_factor


def _inject(net, bus, technologies: list[dict], scale: float, season: str,
            generator_scales: dict) -> list[int]:
    created = []
    for tech in technologies:
        technology = str(tech.get("technology", "wind"))
        seasonal = seasonal_factor(technology, season, generator_scales)
        p = float(tech["P_MW"]) * scale * seasonal
        # Batteries have a signed factor (+ discharge, - charge): the sign moves P, never Q.
        q = float(tech.get("Q_MVAr", 0.0)) * scale * abs(seasonal)
        idx = pp.create_sgen(
            net, bus=bus, p_mw=p, q_mvar=q, name=tech["name"],
            min_q_mvar=float(tech.get("Q_min", -0.5 * abs(p))),
            max_q_mvar=float(tech.get("Q_max", 0.5 * abs(p))),
            controllable=bool(tech.get("controllable", False)),
            in_service=True, type=technology,
        )
        net.sgen.at[idx, "technology"] = technology
        net.sgen.at[idx, "installed_p_mw"] = abs(float(tech["P_MW"]) * scale)
        created.append(idx)
    return created


def _drop_sgens(net, indices):
    net.sgen.drop(index=indices, errors="ignore", inplace=True)
    if "res_sgen" in net:
        net.res_sgen.drop(index=indices, errors="ignore", inplace=True)


def _violations(results, *, max_loading_percent, voltage_change_limit_pu,
                min_voltage_pu, max_voltage_pu, ignored_elements,
                bus_nominal_kv, voltage_limits_min_kv,
                significance_threshold_percent=None):
    return check_violations(
        results,
        max_loading_percent=max_loading_percent,
        voltage_change_limit_pu=voltage_change_limit_pu,
        min_voltage_pu=min_voltage_pu,
        max_voltage_pu=max_voltage_pu,
        ignored_elements=ignored_elements,
        bus_nominal_kv=bus_nominal_kv,
        voltage_limits_min_kv=voltage_limits_min_kv,
        significance_threshold_percent=significance_threshold_percent,
    )


@dataclass
class CapacitySearch:
    """Outcome of the binary search, including what stopped it.

    best_mw           greatest candidate size without significant violations.
    results           N-1 results at best_mw (no significant violations).
    limiting_mw       smallest candidate size found INFEASIBLE (None if none
                      was tested); the true limit lies in (best_mw, limiting_mw].
    limiting_results  N-1 results at limiting_mw.
    blocking_at_limit significant violations at limiting_mw: the bottleneck.
    trials            every evaluated size with its violation counts.
    """
    best_mw: float
    results: list[ContingencyResult]
    original_mw: float
    tolerance_mw: float
    limiting_mw: float | None = None
    limiting_results: list[ContingencyResult] | None = None
    blocking_at_limit: list[Violation] = field(default_factory=list)
    trials: list[dict] = field(default_factory=list)


def find_max_capacity_detailed(net, target_bus, technologies: list[dict],
                      outages: Iterable[Outage], *, season: str,
                      generator_scales: dict, power_flow_options: dict,
                      tolerance_mw: float = 1.0,
                      max_loading_percent: float = 100.0,
                      voltage_change_limit_pu: float = 0.02,
                      min_voltage_pu: float | None = None,
                      max_voltage_pu: float | None = None,
                      ignored_elements: set[ElementRef] | None = None,
                      bus_nominal_kv: Mapping[Hashable, float] | None = None,
                      voltage_limits_min_kv: float | None = None,
                      significance_threshold_percent: float | None = None,
                      progress: Callable[[float, int], None] | None = None
                      ) -> CapacitySearch:
    """Binary search for the greatest feasible capacity, keeping the bottleneck.

    Violations whose base->post change is <= significance_threshold_percent
    are not blocking. The search assumes feasibility is monotonic in size.
    """
    original = sum(float(t["P_MW"]) for t in technologies)
    outages = list(outages)
    options = dict(
        max_loading_percent=max_loading_percent,
        voltage_change_limit_pu=voltage_change_limit_pu,
        min_voltage_pu=min_voltage_pu,
        max_voltage_pu=max_voltage_pu,
        ignored_elements=ignored_elements,
        bus_nominal_kv=bus_nominal_kv,
        voltage_limits_min_kv=voltage_limits_min_kv,
        significance_threshold_percent=significance_threshold_percent,
    )
    trials: list[dict] = []

    def record(size, violations):
        blocking = significant_only(violations)
        trials.append({"trial_mw": size, "feasible": not blocking,
                       "blocking_violations": len(blocking),
                       "all_violations": len(violations)})
        if progress:
            progress(size, len(blocking))
        return blocking

    zero_results = run_contingencies(
        net, outages, target_bus=target_bus,
        power_flow_options=power_flow_options,
    )
    zero_blocking = record(0.0, _violations(zero_results, **options))
    if original <= 0 or zero_blocking:
        return CapacitySearch(0.0, zero_results, original, tolerance_mw,
                              limiting_mw=0.0, limiting_results=zero_results,
                              blocking_at_limit=zero_blocking, trials=trials)

    lo, hi = 0.0, original
    best, best_results = 0.0, zero_results
    limit_results, limit_blocking, limit_mw = None, [], None
    while hi - lo > tolerance_mw:
        trial = (lo + hi) / 2.0
        created = _inject(
            net, target_bus, technologies, trial / original,
            season, generator_scales,
        )
        try:
            results = run_contingencies(
                net, outages, target_bus=target_bus,
                power_flow_options=power_flow_options,
            )
            blocking = record(trial, _violations(results, **options))
        finally:
            _drop_sgens(net, created)
        if blocking:
            hi = trial
            limit_mw, limit_results, limit_blocking = trial, results, blocking
        else:
            lo, best, best_results = trial, trial, results
    return CapacitySearch(best, best_results, original, tolerance_mw,
                          limiting_mw=limit_mw, limiting_results=limit_results,
                          blocking_at_limit=limit_blocking, trials=trials)


def find_max_capacity(net, target_bus, technologies: list[dict],
                      outages: Iterable[Outage], **kwargs
                      ) -> tuple[float, list[ContingencyResult]]:
    """Backward-compatible wrapper: (best capacity, results at that capacity)."""
    search = find_max_capacity_detailed(net, target_bus, technologies, outages, **kwargs)
    return search.best_mw, search.results
