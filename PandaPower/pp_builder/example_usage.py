from pp_builder import NetworkBuilder, LoadFlowRunner
from pp_builder.config import RATING_SEASON_SUMMER, RATING_SEASON_WINTER
from pp_builder.network_topology import report_islands
from pp_builder.network_visualiser import draw_network

# ── 1. Build the network ──────────────────────────────────────────────────────
builder = NetworkBuilder()
net = builder.build(
    excel_path="grid_data.xlsx",
    season=RATING_SEASON_SUMMER,
    output_path="model_summer.json",
)

# ── 2. Topology health-check ──────────────────────────────────────────────────
report_islands(net)

# ── 3. Draw the network ───────────────────────────────────────────────────────
draw_network(net, output_path="network.png", show_labels=True)

# ── 4. Summer load flow ───────────────────────────────────────────────────────
runner = LoadFlowRunner(net)
ok = runner.run(
    season=RATING_SEASON_SUMMER,
    load_scale=1.10,             # 110% of base load
    # per-technology generator overrides (optional):
    tech_gen_overrides={"wind": 0.85, "solar": 1.00, "battery": 1.00},
)
if ok:
    runner.print_summary()
    overloads = runner.overloaded_lines()
    print(f"Overloaded lines:\n{overloads.to_string(index=False)}")

# ── 5. Winter load flow ───────────────────────────────────────────────────────
ok = runner.run(
    season=RATING_SEASON_WINTER,
    load_scale=0.90,
)
if ok:
    runner.print_summary()
