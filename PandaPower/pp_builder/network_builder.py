"""
network_builder.py — orchestrator that calls all sub-builders in order.
"""
from __future__ import annotations
from pathlib import Path
import datetime

import pandapower as pp
from pandapower import to_excel
from pandas import read_excel
import math

from pp_builder.geo_placement import place_unknown_buses
from pp_builder.config import RATING_SEASON_SUMMER
from pp_builder.excel_reader import read_excelsheets
from pp_builder.busbar_builder     import build_busbars
from pp_builder.branch_builder     import build_branches
from pp_builder.transformer_2w_builder import build_transformers
from pp_builder.transformer_3w_builder import build_transformers_3w
from pp_builder.load_builder       import build_loads, scale_loads_to_target
from pp_builder.generator_builder import build_generators, build_mekh_generators, build_hmke_generators
from pp_builder.ext_grid_builder   import build_ext_grids
from pp_builder.shunt_builder      import build_shunts

from .geo_utils import load_bus_geodata, load_line_geodata_from_eov, save_placed_buses_to_excel



class NetworkBuilder:
    """
    Builds a pandapower network from a MAVIR Excel workbook.

    Attributes
    ----------
    net : pp.pandapowerNet
        The constructed network (available after build()).
    bus_map : dict[str, int]
        Busbar name → pandapower bus index.
    branch_map : dict[str, int]
        Branch name → pandapower line index.
    trafo_map : dict[str, int]
        Transformer name → pandapower trafo/trafo3w index.
    load_map : dict[str, int]
        Load name → pandapower load index.
    gen_map : dict[str, tuple[str, int]]
        Generator name → (table, index) where table ∈ {"gen","sgen"}.
    """

    def __init__(self):
        self.net: pp.pandapowerNet | None = None
        self.bus_map: dict[str, int] = {}
        self.branch_map: dict[str, int] = {}
        self.trafo_map: dict[str, int] = {}
        self.trafo3w_map: dict[str, int] = {}
        self.load_map: dict[str, int] = {}
        self.gen_map: dict[str, tuple[str, int]] = {}
        self.ext_grid_map: dict[str, int] = {}
        self.shunt_map: dict[str, int] = {}

    def build(
        self,
        excel_path: str,
        MEKH_excel_path: str,
        HMKE_excel_path: str,
        bus_geo_xlsx:     str  = None,   # ← Geo_Coordinates_busbars.xlsx
        line_geo_csvs:    list[str] = None,  # ← list of EOV CSV files
        season: str = RATING_SEASON_SUMMER,
        output_dir: str | None = None,
        f_hz: float = 50.0,
        sn_mva: float = 100.0,
        target_load_mw: float | None = None,
    ) -> pp.pandapowerNet:
        """
        Read Excel, create pandapower network, optionally save to file.

        Parameters
        ----------
        excel_path : str
            Path to the MAVIR Excel workbook.
        bus_geo_xlsx : str, optional
            Path to the Excel file containing bus geodata.
        line_geo_csvs : list[str], optional
            List of paths to CSV files containing line geodata.
        season : str
            Active seasonal rating: 'summer' or 'winter'.
        output_path : str, optional
            If given, save the network to this path with pp.to_json().
        f_hz : float
            Grid frequency (default 50 Hz).
        sn_mva : float
            System base MVA (default 100 MVA).
        target_load_mw : float, optional
            If given, scale all loads to this total MW value after building and dropping inactive elements.

        Returns
        -------
        pp.pandapowerNet
        """
        print(f"\n{'='*60}")
        print(f"NetworkBuilder: reading {excel_path!r} (season={season})")
        print(f"{'='*60}")

        sheets = read_excelsheets(excel_path)
        MEKH_list = read_excel(MEKH_excel_path, sheet_name="Table 1", header=1)

                # ── Load coordinate data ───────────────────────────────────────────
        coord_map = load_bus_geodata(bus_geo_xlsx) if bus_geo_xlsx else None

        line_geo: dict[str, list] = {}
        if line_geo_csvs:
            for csv_path in line_geo_csvs:
                line_geo.update(load_line_geodata_from_eov(csv_path))

        self.net = pp.create_empty_network(f_hz=f_hz, sn_mva=sn_mva)

        self.bus_map   = build_busbars(self.net, sheets, coord_map=coord_map)
        self.branch_map = build_branches(self.net, sheets, self.bus_map, line_geo=line_geo, season=season)
        self.trafo_map  = build_transformers(self.net, sheets, self.bus_map)
        self.trafo3w_map = build_transformers_3w(self.net, sheets, self.bus_map)
        self.load_map   = build_loads(self.net, sheets, self.bus_map)
        self.gen_map    = build_generators(self.net, sheets, self.bus_map, season=season)
        self.gen_map.update(build_mekh_generators(self.net, MEKH_list, self.bus_map, season=season))
        self.gen_map.update(build_hmke_generators(self.net, HMKE_excel_path, self.bus_map, season=season))
        self.ext_grid_map = build_ext_grids(self.net, sheets, self.bus_map)
        self.shunt_map = build_shunts(self.net, sheets, self.bus_map)

        


        #check_trafo_impedance(self.net)
        #check_trafo3w_impedance(self.net)

        print(f"\n{'='*60}")
        print(f"Network summary:")
        print(f"  Buses         : {len(self.net.bus)}")
        print(f"  Lines         : {len(self.net.line)}")
        print(f"  2WTransformers  : {len(self.net.trafo)}")
        print(f"  3WTransformers  : {len(self.net.trafo3w)}")
        print(f"  Loads         : {len(self.net.load)}")
        print(f"  Generators    : {len(self.net.gen)} gen + {len(self.net.sgen)} sgen")
        print(f"  Ext. grids    : {len(self.net.ext_grid)}")
        print(f"  Shunts        : {len(self.net.shunt)}")
        print(f"{'='*60}\n")

        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")

        if output_dir:
            out = Path(output_dir)
            out.mkdir(parents=True, exist_ok=True)
            pp.to_json(self.net, out / f'rawmodel_{timestamp}.json')
            print(f"[NetworkBuilder]  Saved → {out / f'rawmodel_{timestamp}.json'}")

        pp.drop_inactive_elements(self.net,respect_switches=True)

        if target_load_mw is not None:
            scale_loads_to_target(self.net, target_load_mw, in_service_only=True, respect_existing_scaling=False)

        print(f"\n{'='*60}")
        print(f"Network summary:")
        print(f"  Buses         : {len(self.net.bus)}")
        print(f"  Lines         : {len(self.net.line)}")
        print(f"  2WTransformers  : {len(self.net.trafo)}")
        print(f"  3WTransformers  : {len(self.net.trafo3w)}")
        print(f"  Loads         : {len(self.net.load)}")
        print(f"  Generators    : {len(self.net.gen)} gen + {len(self.net.sgen)} sgen")
        print(f"  Ext. grids    : {len(self.net.ext_grid)}")
        print(f"  Shunts        : {len(self.net.shunt)}")
        print(f"{'='*60}\n")

        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")

                # ── Second-pass geo placement ──────────────────────────────────
        if coord_map is not None:
            placed_busses = place_unknown_buses(self.net, skip_prefix="X")
        #save_placed_buses_to_excel(placed_busses, output_dir="PandaPower/input_data")

        if output_dir:
            out = Path(output_dir)
            out.mkdir(parents=True, exist_ok=True)
            pp.to_json(self.net, out / f'model_{timestamp}.json')
            print(f"[NetworkBuilder]  Saved → {out / f'model_{timestamp}.json'}")

        to_excel(self.net, f"PandaPower/models/model_{timestamp}.xlsx", include_results=True, include_empty_tables=False)

        return self.net
    
    def load(self, json_path: str) -> pp.pandapowerNet:
            """
            Load a previously saved pandapower network from a JSON file.

            Rebuilds the bus_map, branch_map, trafo_map, load_map, and gen_map
            from the loaded network so all builder references stay valid.

            Parameters
            ----------
            json_path : str
                Path to a .json file saved with pp.to_json() or NetworkBuilder.build().

            Returns
            -------
            pp.pandapowerNet
            """
            self.net = pp.from_json(json_path)

            # Rebuild bus_map: name → index
            self.bus_map = {
                str(row["name"]): idx
                for idx, row in self.net.bus.iterrows()
            }

            # Rebuild branch_map: name → index
            self.branch_map = {
                str(row["name"]): idx
                for idx, row in self.net.line.iterrows()
            }

            # Rebuild trafo_map: name → index (2W and 3W combined)
            self.trafo_map = {}
            for idx, row in self.net.trafo.iterrows():
                self.trafo_map[str(row["name"])] = idx
            for idx, row in self.net.trafo3w.iterrows():
                self.trafo_map[str(row["name"])] = idx

            # Rebuild load_map: name → index
            self.load_map = {
                str(row["name"]): idx
                for idx, row in self.net.load.iterrows()
            }

            # Rebuild gen_map: name → ("gen" or "sgen", index)
            self.gen_map = {}
            for idx, row in self.net.gen.iterrows():
                self.gen_map[str(row["name"])] = ("gen", idx)
            for idx, row in self.net.sgen.iterrows():
                self.gen_map[str(row["name"])] = ("sgen", idx)

            # Rebuild ext_grid_map: name → index
            self.ext_grid_map = {
                str(row["name"]): idx
                for idx, row in self.net.ext_grid.iterrows()
            }

            # Rebuild shunt_map: name → index
            self.shunt_map = {
                str(row["name"]): idx
                for idx, row in self.net.shunt.iterrows()
            }

            print(f"\n{'='*60}")
            print(f"[NetworkBuilder]  Loaded '{json_path}'")
            print(f"  Buses        : {len(self.net.bus)}")
            print(f"  Lines        : {len(self.net.line)}")
            print(f"  2WTransformers : {len(self.net.trafo)}")
            print(f"  3WTransformers : {len(self.net.trafo3w)}")
            print(f"  Loads        : {len(self.net.load)}")
            print(f"  Generators   : {len(self.net.gen)} gen + {len(self.net.sgen)} sgen")
            print(f"  Ext. grids   : {len(self.net.ext_grid)}")
            print(f"  Shunts       : {len(self.net.shunt)}")
            print(f"{'='*60}\n")


            return self.net

    

def check_trafo_impedance(net):
    """Print transformers where vkr_percent >= vk_percent."""
    problems = []
    for idx, row in net.trafo.iterrows():
        vk  = row.get("vk_percent",  0.0)
        vkr = row.get("vkr_percent", 0.0)
        if vk <= 0 or math.isnan(vk) or math.isnan(vkr):
            problems.append((idx, row.get("name"), vk, vkr, "vk is zero or NaN"))
        elif vkr >= vk:
            problems.append((idx, row.get("name"), vk, vkr, "vkr >= vk"))
    
    if problems:
        print("Transformer impedance problems:")
        for idx, name, vk, vkr, reason in problems:
            print(f"  trafo[{idx}] '{name}': vk={vk:.4f}%  vkr={vkr:.4f}%  → {reason}")
    else:
        print("All transformer impedances OK.")

def check_trafo3w_impedance(net):
    """Print 3W transformers where any vkr >= vk for HV, MV, or LV winding."""
    problems = []

    for idx, row in net.trafo3w.iterrows():
        name = row.get("name", str(idx))
        pairs = [
            ("HV", row.get("vk_hv_percent",  0.0), row.get("vkr_hv_percent", 0.0)),
            ("MV", row.get("vk_mv_percent",  0.0), row.get("vkr_mv_percent", 0.0)),
            ("LV", row.get("vk_lv_percent",  0.0), row.get("vkr_lv_percent", 0.0)),
        ]
        for winding, vk, vkr in pairs:
            if math.isnan(vk) or math.isnan(vkr):
                problems.append((idx, name, winding, vk, vkr, "NaN value"))
            elif vk <= 0:
                problems.append((idx, name, winding, vk, vkr, "vk is zero or negative"))
            elif vkr >= vk:
                problems.append((idx, name, winding, vk, vkr, "vkr >= vk"))
            elif vkr < 0:
                problems.append((idx, name, winding, vk, vkr, "vkr is negative"))

    if problems:
        print("3W Transformer impedance problems:")
        for idx, name, winding, vk, vkr, reason in problems:
            print(f"  trafo3w[{idx}] '{name}' {winding}-winding: "
                  f"vk={vk:.4f}%  vkr={vkr:.4f}%  → {reason}")
    else:
        print("All 3W transformer impedances OK.")