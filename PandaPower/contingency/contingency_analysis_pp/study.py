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

    @staticmethod
    def _technology_labels(value) -> set[str]:
        if isinstance(value, (list, tuple, set)):
            return {str(item).strip().lower() for item in value}
        return {str(value).strip().lower()}

    def _generator_scale(self, value, season: str) -> float:
        labels = self._technology_labels(value)
        aliases = {
            "solar": {"solar", "solarphotovo", "rooftoppv", "otherres"},
            "wind": {"wind", "windonshore", "windoffshore"},
            "battery": {"battery", "batterystrg"},
        }
        for configured, seasonal in self.config.generator_scales.items():
            configured_labels = aliases.get(str(configured).strip().lower(), {
                str(configured).strip().lower()
            })
            if labels & configured_labels:
                return float(seasonal.get(season, 1.0))
        return 1.0

    def _set_existing_generator_scales(self, season: str) -> None:
        if not self.config.scale_existing_generators:
            return
        for table_name in ("gen", "sgen"):
            if table_name not in self.net:
                continue
            table = self.net[table_name]
            source_column = (
                "technology" if "technology" in table.columns else
                "type" if "type" in table.columns else None
            )
            if source_column is None:
                continue
            if "scaling" not in table.columns:
                table["scaling"] = 1.0
            table.loc[:, "scaling"] = table[source_column].map(
                lambda value: self._generator_scale(value, season)
            )

    def _set_season(self, season: str):
        if season not in self.config.load_scales:
            raise KeyError(f"No load scaling configured for season {season!r}")
        load = self.config.load_scales[season]
        if "scaling" not in self.net.load.columns:
            self.net.load["scaling"] = 1.0
        self.net.load.loc[:, "scaling"] = (
            self.net.load["scaling"].astype(float) * float(load["P"])
        )
        if float(load["P"]) != float(load["Q"]):
            self.net.load.loc[:, "q_mvar"] *= float(load["Q"]) / float(load["P"])
        self._set_existing_generator_scales(season)
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
        original_load = self.net.load.copy(deep=True)
        original_line = self.net.line.copy(deep=True)
        original_gen = self.net.gen.copy(deep=True) if "gen" in self.net else None
        original_sgen = self.net.sgen.copy(deep=True) if "sgen" in self.net else None
        study_results = {}
        try:
            for season in seasons:
                self.net.load = original_load.copy(deep=True)
                self.net.line = original_line.copy(deep=True)
                if original_gen is not None:
                    self.net.gen = original_gen.copy(deep=True)
                if original_sgen is not None:
                    self.net.sgen = original_sgen.copy(deep=True)
                self._set_season(season)
                created = _inject(
                    self.net, self.target_bus, self.technologies, 1.0,
                    season, self.config.generator_scales,
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
                        generator_scales=self.config.generator_scales,
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
                    },
                    study_parameters=self.parameters(),
                )
                study_results[season] = {
                    "max_capacity_mw": max_capacity,
                    "results": results, "violations": violations, "report": report,
                }
        finally:
            self.net.load = original_load
            self.net.line = original_line
            if original_gen is not None:
                self.net.gen = original_gen
            if original_sgen is not None:
                self.net.sgen = original_sgen
            pp.runpp(self.net, **self.config.power_flow_options)
        return study_results
