import pandapower as pp
from contingency_analysis_pp import make_outages, run_contingencies, check_violations

def test_n_1_restores_state_and_base_results():
    net = pp.create_empty_network()
    b0 = pp.create_bus(net, 110, name="A")
    b1 = pp.create_bus(net, 110, name="B")
    for name in ("L1", "L2"):
        pp.create_line_from_parameters(net, b0, b1, 1, 0.05, 0.2, 0, 1, name=name)
    pp.create_ext_grid(net, b0)
    pp.create_load(net, b1, 10, 2)
    outages = make_outages(net, "line")
    results = run_contingencies(net, outages, target_bus=b1)
    assert len(results) == 2
    assert all(r.converged for r in results)
    assert net.line.in_service.all()
    assert net.converged
    assert not check_violations(results, max_loading_percent=1000,
                                voltage_change_limit_pu=1)
