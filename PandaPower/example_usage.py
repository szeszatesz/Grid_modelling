from pp_builder import NetworkBuilder, LoadFlowRunner
from pp_builder.config import RATING_SEASON_SUMMER, RATING_SEASON_WINTER
from pp_builder.network_topology import report_islands
from pp_builder.network_visualiser import draw_network
from pandapower.diagnostic.diagnostic_helpers import diagnostic
from pandapower.diagnostic.diagnostic_functions import MultipleVoltageControllingElementsPerBus

# ── 1. Build the network ──────────────────────────────────────────────────────
builder = NetworkBuilder()
building = True
if building:
    net = builder.build(
        excel_path="PandaPower/2034sn_fp1_tv1_3_none-torf_de_all_v22_celallapot_zarlat_summer_small_load_increase.xlsx",
        season=RATING_SEASON_SUMMER,
        output_dir="/home/attilas/Grid_modelling/PandaPower/models",
    )
else:
    net = builder.load("PandaPower/models/model.json")


diagnose = True
if diagnose:
    checker = MultipleVoltageControllingElementsPerBus()
    print(f"Following buses have multiple controlling elements {checker.diagnostic(net)}")

    # ── 2. Topology health-check ──────────────────────────────────────────────────
    report_islands(net)

    diagnostic(net)

# ── 3. Draw the network ───────────────────────────────────────────────────────
#draw_network(net, output_path="network.png", show_labels=True)

# ── 4. Summer load flow ───────────────────────────────────────────────────────
exe_lf = False
if exe_lf:
    runner = LoadFlowRunner(net)
    ok = runner.run(
        season=RATING_SEASON_SUMMER,
        load_scale=1.10,             # 110% of base load
        # per-technology generator overrides (optional):
        tech_gen_overrides={"WINDONSHORE": 0.85, "SOLARPHOTOVO": 1.00, "BATTERYSTRG": 1.00},
    )
    if ok:
        runner.print_summary()
        overloads = runner.overloaded_lines()
        print(f"Overloaded lines:\n{overloads.to_string(index=False)}")

    ok = runner.run(
        season=RATING_SEASON_WINTER,
        load_scale=0.90,
    )
    if ok:
        runner.print_summary()
