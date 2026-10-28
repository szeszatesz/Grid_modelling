from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any

DEFAULT_GEN_SCALE = {
    "wind": {"summer": 1, "winter": 1},
    "solar": {"summer": 0.5, "winter": 0.20},
    "battery": {"summer": 0, "winter": 0},
}
DEFAULT_LOAD_SCALE = {
    "summer": {"P": 1.0, "Q": 1.0},
    "winter": {"P": 1.0, "Q": 1.0},
}

@dataclass(slots=True)
class StudyConfig:
    """Numerical limits and solver options for an N-1 study."""
    max_loading_percent: float = 100.0
    voltage_change_limit_pu: float = 0.02
    min_voltage_pu: float | None = None
    max_voltage_pu: float | None = None
    capacity_tolerance_mw: float = 1.0
    load_scales: dict[str, dict[str, float]] = field(
        default_factory=lambda: {k: dict(v) for k, v in DEFAULT_LOAD_SCALE.items()}
    )
    generator_scales: dict[str, dict[str, float]] = field(
        default_factory=lambda: {k: dict(v) for k, v in DEFAULT_GEN_SCALE.items()}
    )
    power_flow_options: dict[str, Any] = field(default_factory=lambda: {
        "algorithm": "nr",
        "calculate_voltage_angles": True,
        "enforce_q_lims": True,
        "max_iteration": 50,
        "tolerance_mva": 1e-8,
    })
