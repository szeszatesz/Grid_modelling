from __future__ import annotations
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Iterable
import pandapower as pp

from .config import StudyConfig
from .models import Outage, ElementRef
from .selectors import resolve_one
from .engine import make_outages, run_contingencies
from .violations import check_violations
from .capacity import find_max_capacity, _inject, _drop_sgens
from .reporting import write_report
from .topology import get_relevant_contingency_lines
from model_power_summary import collect_model_power_tables
from .scaling import apply_generator_scaling, battery_mask, seasonal_factor, validate_factor, attach_actual_results

class ContingencyStudy:
    """IPSA-style seasonal N-1 connection-capacity study for pandapower."""
    def __init__(self, net, target_bus, technologies: list[dict], *,
                 outage_elements: Iterable[Outage] | None = None,
                 ignored_elements: set[ElementRef] | None = None,
                 config: StudyConfig | None = None):
        self.net = net
        self.target_bus_selector = deepcopy(target_bus)
        self.target_bus = resolve_one(net, "bus", target_bus)
        self.technologies = deepcopy(technologies)
        self.ignored_elements = ignored_elements or set()
        self.config = config or StudyConfig()
        self.outage_elements_were_explicit = outage_elements is not None
        if outage_elements is None:
            relevant = get_relevant_contingency_lines(
                net, self.target_bus,
                ignored={r.index for r in self.ignored_elements if r.element_type == "line"},
            )
            self.outages = make_outages(net, "line", relevant)
        else:
            self.outages = list(outage_elements)

    def parameters(self) -> dict:
        """Serializable effective parameters, including every default value."""
        return {
            "class": type(self).__name__,
            "target_bus_selector": self.target_bus_selector,
            "target_bus_index": self.target_bus,
            "target_bus_name": self.net.bus.at[self.target_bus, "name"],
            "technologies": deepcopy(self.technologies),
            "outage_elements_were_explicit": self.outage_elements_were_explicit,
            "outages": [asdict(outage) for outage in self.outages],
            "ignored_elements": [asdict(ref) for ref in sorted(
                self.ignored_elements, key=lambda x: (x.element_type, str(x.index))
            )],
            "config": asdict(self.config),
        }

    def _scaling_rules(self):
        rules = deepcopy(self.config.generator_scales)
        if self.config.default_generator_scale is not None:
            factor = validate_factor(self.config.default_generator_scale)
            rules["default"] = {season: factor for season in self.config.load_scales}
        return rules

    def _generator_scale(self, value, season: str) -> float:
        return seasonal_factor(value, season, self._scaling_rules())

    def _set_existing_generator_scales(self, season: str) -> None:
        self.scaling_audit = None
        if self.config.scale_existing_generators:
            self.scaling_audit = apply_generator_scaling(
                self.net, season, self._scaling_rules(),
                basis=self.config.generator_scaling_basis,
                rating_columns=self.config.generator_rating_columns,
                zero_battery_disconnect_gen=self.config.zero_battery_disconnect_gen,
            )

    def _set_season(self, season: str):
        if season not in self.config.load_scales:
            raise KeyError(f"No load scaling configured for season {season!r}")
        if not hasattr(self, "_season_baseline"):
            self._season_baseline = {
                name: self.net[name].copy(deep=True)
                for name in ("load", "line", "gen", "sgen", "storage") if name in self.net
            }
        for name, frame in self._season_baseline.items():
            self.net[name] = frame.copy(deep=True)
        load = self.config.load_scales[season]
        p_factor, q_factor = validate_factor(load["P"]), validate_factor(load["Q"])
        ordinary_loads = ~battery_mask(self.net.load, "load")
        self.net.load.loc[ordinary_loads, "p_mw"] *= p_factor
        self.net.load.loc[ordinary_loads, "q_mvar"] *= q_factor
        self._set_existing_generator_scales(season)
        self.net["converged"] = False
        rating_column = f"max_i_ka_{season}"
        if rating_column in self.net.line.columns:
            self.net.line.loc[:, "max_i_ka"] = self.net.line[rating_column]

    def _check(self, results):
        return check_violations(
            results,
            max_loading_percent=self.config.max_loading_percent,
            voltage_change_limit_pu=self.config.voltage_change_limit_pu,
            min_voltage_pu=self.config.min_voltage_pu,
            max_voltage_pu=self.config.max_voltage_pu,
            ignored_elements=self.ignored_elements,
            bus_nominal_kv=self.net.bus["vn_kv"].to_dict(),
            voltage_limits_min_kv=self.config.voltage_limits_min_kv,
        )

    def run(self, seasons=("summer", "winter"), output_dir="results") -> dict:
        output = Path(output_dir)
        output.mkdir(parents=True, exist_ok=True)
        originals = {
            name: self.net[name].copy(deep=True)
            for name in ("load", "line", "gen", "sgen", "storage") if name in self.net
        }
        self._season_baseline = originals
        study_results = {}
        try:
            for season in seasons:
                self._set_season(season)
                created = _inject(
                    self.net, self.target_bus, self.technologies, 1.0,
                    season, self._scaling_rules(),
                )
                try:
                    results = run_contingencies(
                        self.net, self.outages, target_bus=self.target_bus,
                        power_flow_options=self.config.power_flow_options,
                    )
                    violations = self._check(results)
                finally:
                    _drop_sgens(self.net, created)
                full_capacity = sum(float(t["P_MW"]) for t in self.technologies)
                if violations:
                    max_capacity, results = find_max_capacity(
                        self.net, self.target_bus, self.technologies, self.outages,
                        season=season,
                        generator_scales=self._scaling_rules(),
                        power_flow_options=self.config.power_flow_options,
                        tolerance_mw=self.config.capacity_tolerance_mw,
                        max_loading_percent=self.config.max_loading_percent,
                        voltage_change_limit_pu=self.config.voltage_change_limit_pu,
                        min_voltage_pu=self.config.min_voltage_pu,
                        max_voltage_pu=self.config.max_voltage_pu,
                        ignored_elements=self.ignored_elements,
                        bus_nominal_kv=self.net.bus["vn_kv"].to_dict(),
                        voltage_limits_min_kv=self.config.voltage_limits_min_kv,
                    )
                    violations = self._check(results)
                else:
                    max_capacity = full_capacity
                # Candidate sgens have been removed. Re-solve the scaled background
                # so Grid and actual audit values cannot reuse candidate results.
                background_error = None
                try:
                    pp.runpp(self.net, **self.config.power_flow_options)
                    background_converged = bool(self.net.get("converged", False))
                except Exception as exc:
                    background_converged = False
                    self.net["converged"] = False
                    background_error = f"{type(exc).__name__}: {exc}"
                self.scaling_audit = attach_actual_results(
                    self.net, self.scaling_audit, converged=background_converged)
                stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                target_name = str(self.net.bus.at[self.target_bus, "name"]).strip().replace(" ", "_")
                report = output / f"{stamp}_contingency_{target_name}_{season}.xlsx"
                write_report(
                    report, self.net, results, violations,
                    metadata={
                        "season": season,
                        "target_bus": self.target_bus,
                        "target_name": self.net.bus.at[self.target_bus, "name"],
                        "max_capacity_mw": max_capacity,
                        "contingency_count": len(self.outages),
                        "grid_snapshot": "scaled background N-0; candidate excluded",
                        "grid_operational_mode": "results" if background_converged else "setpoints",
                        "grid_power_flow_converged": background_converged,
                        "grid_power_flow_error": background_error,
                    },
                    study_parameters=self.parameters(),
                    scaling_audit=self.scaling_audit,
                    grid_power_tables=collect_model_power_tables(
                        self.net, operational="results" if background_converged else "setpoints"
                    ),
                )
                study_results[season] = {
                    "max_capacity_mw": max_capacity,
                    "results": results, "violations": violations, "report": report,
                    "scaling_audit": self.scaling_audit,
                }
        finally:
            for name, frame in originals.items():
                self.net[name] = frame
            del self._season_baseline
            pp.runpp(self.net, **self.config.power_flow_options)
        return study_results
