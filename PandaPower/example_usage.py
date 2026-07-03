from pp_builder import NetworkBuilder, LoadFlowRunner
from pp_builder.config import RATING_SEASON_SUMMER, RATING_SEASON_WINTER
from pp_builder.network_topology import report_islands
from pp_builder.network_visualiser import draw_network
from pandapower.diagnostic.diagnostic_helpers import diagnostic
from pandapower.diagnostic.diagnostic_functions import MultipleVoltageControllingElementsPerBus
from pandapower import to_excel

# ── 1. Build the network ──────────────────────────────────────────────────────
builder = NetworkBuilder()
building = False
if building:
    net = builder.build(
        excel_path="PandaPower/2034sn_fp1_tv1_3_none-torf_de_all_v22_celallapot_zarlat_summer_small_load_increase.xlsx",
        season=RATING_SEASON_SUMMER,
        output_dir="/home/attilas/Grid_modelling/PandaPower/models",
    )
    to_excel(net, "PandaPower/models/model.xlsx", include_results=True, include_empty_tables=True)
else:
    net = builder.load("PandaPower/models/model.json")



diagnose = False
if diagnose:
    checker = MultipleVoltageControllingElementsPerBus()
    print(f"Following buses have multiple controlling elements {checker.diagnostic(net)}")

    # ── 2. Topology health-check ──────────────────────────────────────────────────
    report_islands(net)

    diagnostic(net)

# ── 3. Draw the network ───────────────────────────────────────────────────────
#draw_network(net, output_path="network.png", show_labels=True)

# ── 4. Summer load flow ───────────────────────────────────────────────────────
exe_lf = True
if exe_lf:
    runner = LoadFlowRunner(net)
    ok = runner.run(
        season=RATING_SEASON_SUMMER,
        load_scale=1.10,             # 110% of base load
        # per-technology generator overrides (optional):
        tech_gen_overrides={"WINDONSHORE": 0.85, "SOLARPHOTOVO": 1.00, "BATTERYSTRG": 1.00},
        enforce_q_lims=True,
        calculate_voltage_angles=False,
        distributed_slack=True,        
    )
    if ok:
        runner.print_summary()
        overloads = runner.overloaded_lines()
        print(f"Overloaded lines:\n{overloads.to_string(index=False)}")
        to_excel(net, "PandaPower/models/modelwithLF_v1.xlsx", include_results=True, include_empty_tables=True)

    ok = runner.run(
        season=RATING_SEASON_WINTER,
        load_scale=0.90,
    )
    if ok:
        runner.print_summary()
        to_excel(net, "PandaPower/models/modelwithLF_v2.xlsx", include_results=True, include_empty_tables=True)

