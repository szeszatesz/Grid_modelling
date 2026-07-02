"""
test_builders.py — unit tests for all builder modules using a synthetic network.
Run with:  python -m pytest pp_builder/test_builders.py -v
"""
import pytest
import math
import pandas as pd
import pandapower as pp

from .config import (
    SHEET_BUSBARS, SHEET_BRANCHES, SHEET_TRANSFORMERS,
    SHEET_LOADS, SHEET_GENERATORS,
    RATING_SEASON_SUMMER, RATING_SEASON_WINTER,
    TECHNOLOGY_WIND, TECHNOLOGY_GAS,
)
from .busbar_builder     import build_busbars
from .branch_builder     import build_branches, set_line_ratings
from .transformer_builder import build_transformers
from .load_builder       import build_loads, apply_load_scaling
from .generator_builder  import build_generators, apply_gen_scaling
from .network_topology   import find_islands, report_islands


# ── Minimal synthetic sheets ──────────────────────────────────────────────────

def make_sheets():
    busbars = pd.DataFrame({
        "Name":       ["BUS_HV", "BUS_132", "BUS_LV", "BUS_ISLAND"],
        "NomVoltkV":  [400.0, 132.0, 33.0, 132.0],
        "IsSlack":    [1, 0, 0, 0],
        "SlackVmPU":  [1.02, float("nan"), float("nan"), float("nan")],
        "InService":  [1, 1, 1, 1],
    })
    branches = pd.DataFrame({
        "Name":                  ["LINE_HV_132"],
        "FromBus":               ["BUS_HV"],
        "ToBus":                 ["BUS_132"],
        "ResistanceOhmPerKm":    [0.1],
        "ReactanceOhmPerKm":     [0.4],
        "CapacitanceNFPerKm":    [9.0],
        "LengthKm":              [50.0],
        "MaxIkA":                [0.0],
        "RatingMVA_summer":      [500.0],
        "RatingMVA_winter":      [600.0],
        "InService":             [1],
        "Parallel":              [1],
        "LineType":              ["ol"],
    })
    transformers = pd.DataFrame({
        "Name":        ["TRAFO_132_33"],
        "HVBus":       ["BUS_132"],
        "LVBus":       ["BUS_LV"],
        "SnMVA":       [63.0],
        "VnHVkV":      [132.0],
        "VnLVkV":      [33.0],
        "VkPercent":   [12.0],
        "VkrPercent":  [0.4],
        "PfeKW":       [50.0],
        "I0Percent":   [0.1],
        "InService":   [1],
    })
    loads = pd.DataFrame({
        "Name":          ["LOAD_LV"],
        "Bus":           ["BUS_LV"],
        "PMW":           [20.0],
        "QMVAr":         [5.0],
        "InService":     [1],
        "ScalingFactor": [1.0],
    })
    generators = pd.DataFrame({
        "Name":          ["GEN_WIND", "GEN_GAS"],
        "Bus":           ["BUS_132", "BUS_HV"],
        "PMW":           [50.0, 100.0],
        "VmPU":          [float("nan"), 1.02],
        "Technology":    [TECHNOLOGY_WIND, TECHNOLOGY_GAS],
        "InService":     [1, 1],
        "QMVAr":         [0.0, float("nan")],
        "SnMVA":         [60.0, 150.0],
    })
    return {
        SHEET_BUSBARS:      busbars,
        SHEET_BRANCHES:     branches,
        SHEET_TRANSFORMERS: transformers,
        SHEET_LOADS:        loads,
        SHEET_GENERATORS:   generators,
    }


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def net_and_maps():
    sheets = make_sheets()
    net = pp.create_empty_network(f_hz=50.0, sn_mva=100.0)
    bus_map   = build_busbars(net, sheets)
    branch_map = build_branches(net, sheets, bus_map, RATING_SEASON_SUMMER)
    trafo_map  = build_transformers(net, sheets, bus_map)
    load_map   = build_loads(net, sheets, bus_map)
    gen_map    = build_generators(net, sheets, bus_map, RATING_SEASON_SUMMER)
    return net, bus_map, branch_map, trafo_map, load_map, gen_map


# ── Tests ─────────────────────────────────────────────────────────────────────

class TestBusbars:
    def test_bus_count(self, net_and_maps):
        net, bus_map, *_ = net_and_maps
        assert len(net.bus) == 4

    def test_slack_ext_grid(self, net_and_maps):
        net, bus_map, *_ = net_and_maps
        assert len(net.ext_grid) == 1
        slack_bus = net.ext_grid.iloc[0]["bus"]
        assert net.bus.at[slack_bus, "name"] == "BUS_HV"

    def test_voltage_levels(self, net_and_maps):
        net, bus_map, *_ = net_and_maps
        assert net.bus.at[bus_map["BUS_HV"],  "vn_kv"] == 400.0
        assert net.bus.at[bus_map["BUS_132"], "vn_kv"] == 132.0
        assert net.bus.at[bus_map["BUS_LV"],  "vn_kv"] == 33.0


class TestBranches:
    def test_line_count(self, net_and_maps):
        net, _, branch_map, *_ = net_and_maps
        assert len(net.line) == 1

    def test_summer_rating_stored(self, net_and_maps):
        net, *_ = net_and_maps
        expected = 500.0 / (math.sqrt(3) * 400.0)
        assert abs(net.line.at[0, "max_i_ka_summer"] - expected) < 1e-6

    def test_winter_rating_stored(self, net_and_maps):
        net, *_ = net_and_maps
        expected = 600.0 / (math.sqrt(3) * 400.0)
        assert abs(net.line.at[0, "max_i_ka_winter"] - expected) < 1e-6

    def test_set_line_ratings_winter(self, net_and_maps):
        net, *_ = net_and_maps
        set_line_ratings(net, RATING_SEASON_WINTER)
        expected = net.line.at[0, "max_i_ka_winter"]
        assert net.line.at[0, "max_i_ka"] == expected


class TestTransformers:
    def test_trafo_count(self, net_and_maps):
        net, *_ = net_and_maps
        assert len(net.trafo) == 1

    def test_trafo_params(self, net_and_maps):
        net, *_ = net_and_maps
        assert net.trafo.at[0, "sn_mva"]      == 63.0
        assert net.trafo.at[0, "vk_percent"]  == 12.0


class TestLoads:
    def test_load_count(self, net_and_maps):
        net, *_, load_map, _ = net_and_maps
        assert len(net.load) == 1

    def test_load_values(self, net_and_maps):
        net, *_ = net_and_maps
        assert net.load.at[0, "p_mw"]   == 20.0
        assert net.load.at[0, "q_mvar"] == 5.0

    def test_apply_load_scaling(self, net_and_maps):
        net, *_ = net_and_maps
        apply_load_scaling(net, 1.10)
        assert net.load.at[0, "scaling"] == 1.10


class TestGenerators:
    def test_sgen_for_wind(self, net_and_maps):
        net, _, _, _, _, gen_map = net_and_maps
        table, idx = gen_map["GEN_WIND"]
        assert table == "sgen"
        assert net.sgen.at[idx, "p_mw"] == 50.0

    def test_gen_for_gas(self, net_and_maps):
        net, _, _, _, _, gen_map = net_and_maps
        table, idx = gen_map["GEN_GAS"]
        assert table == "gen"
        assert net.gen.at[idx, "p_mw"] == 100.0

    def test_wind_technology_stored(self, net_and_maps):
        net, _, _, _, _, gen_map = net_and_maps
        _, idx = gen_map["GEN_WIND"]
        assert net.sgen.at[idx, "technology"] == TECHNOLOGY_WIND

    def test_apply_gen_scaling_winter(self, net_and_maps):
        net, _, _, _, _, gen_map = net_and_maps
        apply_gen_scaling(net, RATING_SEASON_WINTER)
        _, idx = gen_map["GEN_WIND"]
        from .config import TECH_SCALING_DEFAULTS
        expected = TECH_SCALING_DEFAULTS[TECHNOLOGY_WIND][RATING_SEASON_WINTER]
        assert abs(net.sgen.at[idx, "scaling"] - expected) < 1e-9


class TestTopology:
    def test_two_islands(self, net_and_maps):
        net, *_ = net_and_maps
        islands = find_islands(net)
        # BUS_ISLAND is disconnected → 2 islands
        assert len(islands) == 2

    def test_island_sizes(self, net_and_maps):
        net, bus_map, *_ = net_and_maps
        islands = find_islands(net)
        sizes = sorted(len(i) for i in islands)
        assert sizes == [1, 3]   # [BUS_ISLAND], [BUS_HV, BUS_132, BUS_LV]
