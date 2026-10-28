import math
import pandapower as pp
from contingency_analysis_pp import (
    add_busbar_to_line, delete_busbars, modify_elements,
    switch_off, switch_on, temporary_grid,
)

def network():
    net = pp.create_empty_network()
    b0 = pp.create_bus(net, 110, name="A")
    b1 = pp.create_bus(net, 110, name="B")
    line = pp.create_line_from_parameters(net, b0, b1, 10.0, 0.1, 0.4, 10, 1.0,
                                          name="L")
    pp.create_ext_grid(net, b0)
    pp.create_load(net, b1, 10, 2)
    return net, b0, b1, line

def test_split_preserves_total_parameters_and_switches():
    net, b0, b1, old = network()
    sw0 = pp.create_switch(net, b0, old, et="l", closed=True)
    sw1 = pp.create_switch(net, b1, old, et="l", closed=True)
    bus, first, second = add_busbar_to_line(net, "L", 0.25)
    assert old not in net.line.index
    assert math.isclose(net.line.at[first, "length_km"], 2.5)
    assert math.isclose(net.line.at[second, "length_km"], 7.5)
    assert net.switch.at[sw0, "element"] == first
    assert net.switch.at[sw1, "element"] == second
    assert net.line.at[first, "to_bus"] == bus
    assert net.line.at[second, "from_bus"] == bus
    pp.runpp(net)
    assert net.converged

def test_select_modify_service_and_transaction():
    net, _, _, line = network()
    modify_elements(net, "line", "L", max_i_ka=2.0)
    assert net.line.at[line, "max_i_ka"] == 2.0
    with temporary_grid(net):
        switch_off(net, "line", "L")
        assert not net.line.at[line, "in_service"]
    assert net.line.at[line, "in_service"]
    switch_off(net, "line", line)
    switch_on(net, "line", line)
    assert net.line.at[line, "in_service"]
    sw = pp.create_switch(net, net.line.at[line, "from_bus"], line, et="l")
    switch_off(net, "switch", sw)
    assert not net.switch.at[sw, "closed"]
    switch_on(net, "switch", sw)
    assert net.switch.at[sw, "closed"]

def test_split_position_in_km():
    net, _, _, _ = network()
    _, first, second = add_busbar_to_line(net, "L", 4.0, position_unit="km")
    assert math.isclose(net.line.at[first, "length_km"], 4.0)
    assert math.isclose(net.line.at[second, "length_km"], 6.0)

def test_delete_bus_cascades():
    net, _, b1, _ = network()
    delete_busbars(net, b1)
    assert b1 not in net.bus.index
    assert net.line.empty and net.load.empty
