"""Adapt paths and element names, then run from the PandaPower directory."""
from pp_builder.network_builder import NetworkBuilder
from contingency.contingency_analysis_pp import (
    ContingencyStudy, StudyConfig, ElementRef,
    add_busbar_to_line, modify_elements, switch_off, switch_on,
)

builder = NetworkBuilder()
net = builder.load("PandaPower/models/model_20261019_194732.json")

# Generic edits before the study (examples; uncomment and adapt):
# modify_elements(net, "line", "LINE_A", max_i_ka=1.50)
# modify_elements(net, "load", {"name": ["LOAD_A", "LOAD_B"]}, scaling=0.95)
# switch_off(net, "trafo", "TRAFO_1")
# switch_on(net, "trafo", "TRAFO_1")
# new_bus, line_1, line_2 = add_busbar_to_line(net, "LINE_A", 0.35,
#                                               name="NEW 132 kV BUS")

study = ContingencyStudy(
    net=net,
    target_bus="ALBF B1     120.00",  # exact name, bus index, mapping or query
    technologies=[
        {"name": "WIND_NEW", "technology": "wind", "P_MW": 50.0},
        {"name": "BATTERY_NEW", "technology": "battery", "P_MW": 20.0},
    ],
    ignored_elements=set(),        # ElementRef("line", 123)},
    config=StudyConfig(
        max_loading_percent=100.0,
        voltage_change_limit_pu=0.02,
        min_voltage_pu=0.90,
        max_voltage_pu=1.10,
        capacity_tolerance_mw=1.0,
        power_flow_options={
            "algorithm": "nr",
            "calculate_voltage_angles": False,
            "enforce_q_lims": True,
            "distributed_slack": True,
            "max_iteration": 50,
            "tolerance_mva": 1e-8,
        },
    ),
)

results = study.run(seasons=("summer", "winter"),
                    output_dir="PandaPower/contingency/results")
for season, data in results.items():
    print(season, data["max_capacity_mw"], data["report"])
