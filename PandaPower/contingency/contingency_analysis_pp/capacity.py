from __future__ import annotations
from typing import Callable, Iterable, Mapping, Hashable
import pandapower as pp

from .models import Outage, ContingencyResult, ElementRef
from .engine import run_contingencies
from .violations import check_violations, significant_only
from .scaling import seasonal_factor


def _inject(net, bus, technologies: list[dict], scale: float, season: str,
            generator_scales: dict) -> list[int]:
    created = []
    for tech in technologies:
        technology = str(tech.get("technology", "wind"))
        seasonal = seasonal_factor(technology, season, generator_scales)
        p = float(tech["P_MW"]) * scale * seasonal
        q = float(tech.get("Q_MVAr", 0.0)) * scale * seasonal
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


def find_max_capacity(net, target_bus, technologies: list[dict],
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
                      ) -> tuple[float, list[ContingencyResult]]:
    """Find the greatest feasible project capacity.

    Violations whose base->post change is <= significance_threshold_percent
    are NOT counted as blocking (they remain reportable via check_violations).
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

    zero_results = run_contingencies(
        net, outages, target_bus=target_bus,
        power_flow_options=power_flow_options,
    )
    zero_blocking = significant_only(_violations(zero_results, **options))
    if progress:
        progress(0.0, len(zero_blocking))
    if original <= 0 or zero_blocking:
        return 0.0, zero_results

    lo, hi = 0.0, original
    best, best_results = 0.0, zero_results
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
            blocking = significant_only(_violations(results, **options))
        finally:
            _drop_sgens(net, created)
        if progress:
            progress(trial, len(blocking))
        if blocking:
            hi = trial
        else:
            lo, best, best_results = trial, trial, results
    return best, best_results
