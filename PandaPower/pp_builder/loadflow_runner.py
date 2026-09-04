"""
loadflow_runner.py — wraps pandapower.runpp() with seasonal and
scaling configuration, and provides result helper methods.
"""
from __future__ import annotations

import pandas as pd
import pandapower as pp

from .config import RATING_SEASON_SUMMER
#from .branch_builder    import set_line_ratings    #not used as always summer rating is used
from .load_builder      import apply_load_scaling
from .generator_builder import apply_gen_scaling


class LoadFlowRunner:
    """
    Configures and executes pandapower load flow runs.

    Parameters
    ----------
    net : pp.pandapowerNet
        A network built with NetworkBuilder.build().
    """

    def __init__(self, net: pp.pandapowerNet):
        self.net = net
        self.last_season: str | None = None

    def run(
        self,
        season: str = RATING_SEASON_SUMMER,
        load_scale: float = 1.0,
        tech_gen_overrides: dict[str, float] | None = None,
        algorithm: str = "nr",
        calculate_voltage_angles: bool = True,
        enforce_q_lims: bool = False,
        distributed_slack: bool = False,
        max_iteration: int = 50,
        tolerance_mva: float = 1e-8,
    ) -> bool:
        """
        Apply seasonal settings, scaling, then run the power flow.

        Parameters
        ----------
        season : str
            'summer' or 'winter'.
        load_scale : float
            Global load scaling factor (applied to ALL loads).
        tech_gen_overrides : dict, optional
            {technology: factor} — per-technology generator scaling override.
        algorithm : str
            pandapower solver: 'nr' (Newton-Raphson) or 'bfsw'.
        calculate_voltage_angles : bool
            True for meshed HV networks.
        enforce_q_lims : bool
            Enforce reactive power limits of generators.

        Returns
        -------
        bool
            True if converged.
        """
        # 1. Switch line ratings
        #set_line_ratings(self.net, season)

        # 2. Apply load scaling
        # deleted, as load scaling is applied in the build function, and not needed here

        # 3. Apply generator scaling
        apply_gen_scaling(self.net, season, tech_overrides=tech_gen_overrides)

        self.last_season = season

        # 4. Run
        try:
            pp.runpp(
                self.net,
                algorithm=algorithm,
                calculate_voltage_angles=calculate_voltage_angles,
                enforce_q_lims=enforce_q_lims,
                max_iteration=max_iteration,
                tolerance_mva=tolerance_mva,
                distributed_slack=distributed_slack
            )
            converged = self.net["converged"]
        except pp.powerflow.LoadflowNotConverged:
            print("[LoadFlowRunner]  *** Load flow DID NOT CONVERGE ***")
            return False

        status = "CONVERGED" if converged else "NOT CONVERGED"
        print(f"[LoadFlowRunner]  Load flow {status} "
              f"(season={season}, load_scale={load_scale:.2f}).")
        return converged

    # ── Result helpers ────────────────────────────────────────────────────────

    def bus_voltages(self) -> pd.DataFrame:
        """Return net.res_bus (vm_pu, va_degree, p_mw, q_mvar) sorted by name."""
        res = self.net.res_bus.copy()
        res["name"] = self.net.bus["name"]
        res["vn_kv"] = self.net.bus["vn_kv"]
        return res[["name", "vn_kv", "vm_pu", "va_degree", "p_mw", "q_mvar"]]

    def line_loadings(self) -> pd.DataFrame:
        """Return net.res_line with name, loading_percent, i_ka."""
        res = self.net.res_line[["p_from_mw", "q_from_mvar",
                                  "p_to_mw",   "q_to_mvar",
                                  "i_ka", "loading_percent"]].copy()
        res["name"]     = self.net.line["name"]
        res["max_i_ka"] = self.net.line["max_i_ka"]
        res["loading_pu"] = res["loading_percent"] / 100.0
        return res

    def trafo_loadings(self) -> pd.DataFrame:
        """Return 2W and 3W transformer loadings combined."""
        frames = []
        if not self.net.res_trafo.empty:
            t2 = self.net.res_trafo[["loading_percent"]].copy()
            t2["name"]  = self.net.trafo["name"]
            t2["table"] = "trafo"
            frames.append(t2)
        if not self.net.res_trafo3w.empty:
            t3 = self.net.res_trafo3w[["loading_percent"]].copy()
            t3["name"]  = self.net.trafo3w["name"]
            t3["table"] = "trafo3w"
            frames.append(t3)
        if frames:
            out = pd.concat(frames, ignore_index=True)
            out["loading_pu"] = out["loading_percent"] / 100.0
            return out
        return pd.DataFrame(columns=["name", "table", "loading_percent", "loading_pu"])

    def overloaded_lines(self, threshold_pu: float = 1.0) -> pd.DataFrame:
        """Return lines with loading_pu >= threshold_pu."""
        ll = self.line_loadings()
        return ll[ll["loading_pu"] >= threshold_pu].sort_values(
            "loading_pu", ascending=False)

    def print_summary(self, top_n: int = 20) -> None:
        """Print a brief load-flow summary to stdout."""
        buses = self.bus_voltages()
        lines = self.line_loadings()
        print(f"\n{'─'*60}")
        print(f"Load Flow Summary  (season={self.last_season})")
        print(f"{'─'*60}")
        print(f"Voltage range : "
              f"{buses['vm_pu'].min():.4f} – {buses['vm_pu'].max():.4f} pu")
        overloaded = lines[lines["loading_pu"] >= 1.1]
        print(f"Overloaded lines : {len(overloaded)}")
        if not overloaded.empty:
            print(overloaded[["name", "loading_pu", "i_ka", "max_i_ka"]]
                  .head(top_n).to_string(index=False))
        print(f"{'─'*60}\n")
