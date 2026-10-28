from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Hashable
import math

@dataclass(frozen=True, slots=True)
class ElementRef:
    element_type: str
    index: Hashable

@dataclass(frozen=True, slots=True)
class Outage:
    element_type: str
    index: Hashable
    name: str | None = None

    @property
    def case_id(self) -> str:
        label = self.name if self.name not in (None, "") else self.index
        return f"{self.element_type}:{label}"

@dataclass(slots=True)
class NetworkSnapshot:
    converged: bool
    bus_vm_pu: dict[Hashable, float] = field(default_factory=dict)
    line_loading_percent: dict[Hashable, float] = field(default_factory=dict)
    trafo_loading_percent: dict[Hashable, float] = field(default_factory=dict)
    trafo3w_loading_percent: dict[Hashable, float] = field(default_factory=dict)

@dataclass(slots=True)
class ContingencyResult:
    outage: Outage
    converged: bool
    base: NetworkSnapshot
    post: NetworkSnapshot
    target_bus: Hashable | None = None
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def base_v_target_pu(self) -> float:
        if self.target_bus is None:
            return math.nan
        return self.base.bus_vm_pu.get(self.target_bus, math.nan)

    @property
    def post_v_target_pu(self) -> float:
        if self.target_bus is None:
            return math.nan
        return self.post.bus_vm_pu.get(self.target_bus, math.nan)

    @property
    def voltage_change_pu(self) -> float:
        if math.isnan(self.base_v_target_pu) or math.isnan(self.post_v_target_pu):
            return math.nan
        return abs(self.post_v_target_pu - self.base_v_target_pu)

    def overloaded(self, limit_percent: float = 100.0) -> dict[ElementRef, float]:
        answer: dict[ElementRef, float] = {}
        for table, values in (
            ("line", self.post.line_loading_percent),
            ("trafo", self.post.trafo_loading_percent),
            ("trafo3w", self.post.trafo3w_loading_percent),
        ):
            answer.update({ElementRef(table, idx): value for idx, value in values.items()
                           if value > limit_percent})
        return answer
