from __future__ import annotations
from dataclasses import dataclass
from typing import Hashable, Iterable, Mapping
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
    base_value: float | None = None
    post_value: float | None = None
    delta_value: float | None = None
    pre_existing: bool | None = None


def _branch_base_value(result: ContingencyResult, ref: ElementRef) -> float | None:
    values = {
        "line": result.base.line_loading_percent,
        "trafo": result.base.trafo_loading_percent,
        "trafo3w": result.base.trafo3w_loading_percent,
    }.get(ref.element_type, {})
    return values.get(ref.index)


def check_violations(results: Iterable[ContingencyResult], *,
                     max_loading_percent: float = 100.0,
                     voltage_change_limit_pu: float = 0.02,
                     min_voltage_pu: float | None = None,
                     max_voltage_pu: float | None = None,
                     ignored_elements: set[ElementRef] | None = None,
                     bus_nominal_kv: Mapping[Hashable, float] | None = None,
                     voltage_limits_min_kv: float | None = None) -> list[Violation]:
    """Return post-contingency violations with base/post/delta audit values.

    Absolute under/overvoltage checks can be limited by nominal voltage. With
    ``voltage_limits_min_kv=36`` the 35 kV and lower network is omitted, while
    target-bus voltage-change checks remain active at every voltage level.
    """
    ignored = ignored_elements or set()
    nominal_kv = bus_nominal_kv or {}
    violations: list[Violation] = []
    for result in results:
        case = result.outage.case_id
        if not result.converged:
            violations.append(Violation(
                case, "non_convergence", None, None, None, None,
                result.error or "Power flow did not converge"
            ))
            continue
        for ref, post in result.overloaded(max_loading_percent).items():
            if ref in ignored:
                continue
            base = _branch_base_value(result, ref)
            delta = None if base is None else post - base
            violations.append(Violation(
                case, "overload", ref.element_type, ref.index, post,
                max_loading_percent,
                f"{ref.element_type}[{ref.index}] loaded to {post:.2f}%",
                base, post, delta,
                None if base is None else base > max_loading_percent,
            ))
        delta = result.voltage_change_pu
        if not math.isnan(delta) and delta > voltage_change_limit_pu:
            base = result.base_v_target_pu
            post = result.post_v_target_pu
            violations.append(Violation(
                case, "target_voltage_change", "bus", result.target_bus,
                delta, voltage_change_limit_pu,
                f"Target voltage changed by {delta:.5f} pu",
                base, post, post - base, False,
            ))
        for bus, post in result.post.bus_vm_pu.items():
            ref = ElementRef("bus", bus)
            if ref in ignored:
                continue
            vn_kv = nominal_kv.get(bus)
            if (voltage_limits_min_kv is not None and vn_kv is not None
                    and float(vn_kv) < float(voltage_limits_min_kv)):
                continue
            base = result.base.bus_vm_pu.get(bus)
            delta = None if base is None else post - base
            if min_voltage_pu is not None and post < min_voltage_pu:
                violations.append(Violation(
                    case, "undervoltage", "bus", bus, post, min_voltage_pu,
                    f"bus[{bus}] voltage is {post:.5f} pu",
                    base, post, delta,
                    None if base is None else base < min_voltage_pu,
                ))
            if max_voltage_pu is not None and post > max_voltage_pu:
                violations.append(Violation(
                    case, "overvoltage", "bus", bus, post, max_voltage_pu,
                    f"bus[{bus}] voltage is {post:.5f} pu",
                    base, post, delta,
                    None if base is None else base > max_voltage_pu,
                ))
    return violations
