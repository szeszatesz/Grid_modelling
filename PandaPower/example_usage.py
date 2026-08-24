from pp_builder import NetworkBuilder, LoadFlowRunner
from pp_builder.config import DEFAULT_LOAD_SCALING, RATING_SEASON_SUMMER, RATING_SEASON_WINTER
from pp_builder.network_topology import report_islands
from pp_builder.network_visualiser import draw_network
from pandapower import to_excel, to_excel_with_names
from pandapower.diagnostic.diagnostic_helpers import diagnostic
from pandapower.diagnostic.diagnostic_functions import MultipleVoltageControllingElementsPerBus
from pandapower.plotting.plotly import simple_plotly, vlevel_plotly, pf_res_plotly
import datetime


# ── 1. Build the network ──────────────────────────────────────────────────────
builder = NetworkBuilder()
building = True
if building:
    net = builder.build(
        excel_path="PandaPower/input_data/2034sn_fp1_tv1_3_none-torf_de_all_v22_celallapot_zarlat_summer_small_load_increase.xlsx",
        MEKH_excel_path="PandaPower/input_data/2026-07-30_RES_above_500kW_production_projects.xlsx",
        HMKE_excel_path="PandaPower/input_data/HMKE_2025_Statisztika.xlsx",
        bus_geo_xlsx="PandaPower/input_data/Geo_Coordinates.xlsx",
        line_geo_csvs=["PandaPower/input_data/GRIDMODELL/400kV_pont.csv"],
        season=RATING_SEASON_SUMMER,
        output_dir="/home/attilas/Grid_modelling/PandaPower/models",
        f_hz=50.0,
        sn_mva=100.0,
        target_load_mw=6400.0,  # Scale all loads to this total MW value
    )
else:
    net = builder.load("PandaPower/models/model_20260727_142428.json")

timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")

diagnose = False
if diagnose:
    checker = MultipleVoltageControllingElementsPerBus()
    print(f"Following buses have multiple controlling elements {checker.diagnostic(net)}")

    # ── 2. Topology health-check ──────────────────────────────────────────────────
    report_islands(net)

    diagnostic(net)



# ── 4. Summer load flow ───────────────────────────────────────────────────────
exe_lf = True
if exe_lf:
    runner = LoadFlowRunner(net)
    ok = runner.run(
        season=RATING_SEASON_SUMMER,
        load_scale=DEFAULT_LOAD_SCALING,             # 1.0 = no scaling. Use this as load scaling happenes a modell generation step, not in the load flow step.
        # per-technology generator overrides (optional):
        tech_gen_overrides={"WINDONSHORE": 0.85, "SOLARPHOTOVO": 1.00, "BATTERYSTRG": 1.00},
        enforce_q_lims=True,
        calculate_voltage_angles=False,
        distributed_slack=True,        
    )
    if ok:
        runner.print_summary()
        overloads = runner.overloaded_lines(threshold_pu=1.1)
        print(f"Overloaded lines:\n{overloads.to_string(index=False)}")
        to_excel_with_names(net, filename=f"PandaPower/models/modelwithLF_{timestamp}.xlsx", include_empty_tables=False)

    """ok = runner.run(
        season=RATING_SEASON_WINTER,
        load_scale=0.90,
    )
    if ok:
        runner.print_summary()
        to_excel(net, filename=f"PandaPower/models/modelwithLF_{timestamp}.xlsx", include_results=True, include_empty_tables=False)"""

# ── 3. Draw the network ───────────────────────────────────────────────────────

#draw_network(net, output_path="network_bus_cord.png", show_labels=True)
#simple_plotly(net,filename=f"PandaPower/models/network_simple_plotly_{timestamp}.html")
vlevel_plotly(net, filename=f"PandaPower/models/network_vlevel_plotly_{timestamp}.html", auto_open=False, bus_size=10, line_width=1, respect_switches=False, zoomlevel=8, use_line_geo=False, on_map=False, map_style='basic', projection='23700')
#pf_res_plotly(net, filename=f"PandaPower/models/network_vlevel_plotly_{timestamp}.html", auto_open=False, bus_size=10, line_width=1, zoomlevel=8, use_line_geo=False, on_map=True, map_style='basic')
