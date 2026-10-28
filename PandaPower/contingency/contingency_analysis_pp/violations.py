from __future__ import annotations
from dataclasses import dataclass
from typing import Hashable, Iterable
import math
from .models import ContingencyResult, ElementRef

@dataclass(frozen=True, slots=True)
class Violation:
    case_id: str
    kind: str
    element_type: str | None
    element_index: Hashable | None
    value: float | None
    limit: float | None
    message: str

def check_violations(results: Iterable[ContingencyResult], *,
                     max_loading_percent: float = 100.0,
                     voltage_change_limit_pu: float = 0.02,
                     min_voltage_pu: float | None = None,
                     max_voltage_pu: float | None = None,
                     ignored_elements: set[ElementRef] | None = None) -> list[Violation]:
    ignored = ignored_elements or set()
    violations: list[Violation] = []
    for result in results:
        case = result.outage.case_id
        if not result.converged:
            violations.append(Violation(case, "non_convergence", None, None, None, None,
                                        result.error or "Power flow did not converge"))
            continue
        for ref, value in result.overloaded(max_loading_percent).items():
            if ref not in ignored:
                violations.append(Violation(case, "overload", ref.element_type, ref.index,
                                            value, max_loading_percent,
                                            f"{ref.element_type}[{ref.index}] loaded to {value:.2f}%"))
        delta = result.voltage_change_pu
        if not math.isnan(delta) and delta > voltage_change_limit_pu:
            violations.append(Violation(case, "target_voltage_change", "bus", result.target_bus,
                                        delta, voltage_change_limit_pu,
                                        f"Target voltage changed by {delta:.5f} pu"))
        for bus, vm in result.post.bus_vm_pu.items():
            if min_voltage_pu is not None and vm < min_voltage_pu:
                violations.append(Violation(case, "undervoltage", "bus", bus, vm,
                                            min_voltage_pu, f"bus[{bus}] voltage is {vm:.5f} pu"))
            if max_voltage_pu is not None and vm > max_voltage_pu:
                violations.append(Violation(case, "overvoltage", "bus", bus, vm,
                                            max_voltage_pu, f"bus[{bus}] voltage is {vm:.5f} pu"))
    return violations
