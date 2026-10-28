# Pandapower contingency analysis

This package ports the explicit IPSA workflow to pandapower: solve N-0, open one
selected element, solve N-1, retain complete per-case results, restore the model,
check thermal/voltage/convergence limits, and optionally binary-search connection
capacity.

## Main API

```python
from contingency_analysis_pp import *

# Select by index, exact name, mapping, pandas query, or row predicate.
modify_elements(net, "line", "LINE_A", max_i_ka=1.4)
modify_elements(net, "bus", "vn_kv >= 220 and in_service", max_vm_pu=1.08,
                allow_new_columns=True)
switch_off(net, "line", [10, 11])
switch_on(net, "line", [10, 11])
set_switches_closed(net, {"et": "l", "element": 10}, closed=False)
delete_busbars(net, {"name": "BUS_TO_REMOVE"})
new_bus, segment_a, segment_b = add_busbar_to_line(net, "LINE_A", 0.35)
# Or place it 12.5 km from the line's from_bus:
new_bus, segment_a, segment_b = add_busbar_to_line(
    net, "LINE_B", 12.5, position_unit="km")
```

`switch_off/on` changes an element's `in_service` state. Use
`set_switches_closed` for physical rows in `net.switch`. Bus deletion cascades to
connected elements. Line splitting preserves total series impedance, total shunt
admittance, line rating, custom columns, endpoint switches, and available geodata.

## Direct N-1 run

```python
outages = make_outages(net, "line", "vn_kv >= 132")  # or list of indices
results = run_contingencies(net, outages, target_bus=target_bus,
                            power_flow_options={"algorithm": "nr"})
violations = check_violations(results, max_loading_percent=100,
                              voltage_change_limit_pu=0.02)
write_report("results/n_1.xlsx", net, results, violations)
```

## Transactional edits

```python
with temporary_grid(net):
    switch_off(net, "line", "LINE_A")
    modify_elements(net, "load", None, scaling=1.1)
    pp.runpp(net)
# The exact original network is restored here, even after an exception.
```

Use `GridTransaction` and call `commit()` if edits should persist.

## Assumptions

- Candidate projects are modelled as `sgen` elements (PQ injections).
- Binary search assumes feasibility decreases monotonically with injected MW.
- Pandapower has one `load.scaling` value for both P and Q. The study adjusts
  `q_mvar` only when configured P/Q seasonal factors differ.
- The default relevant-line selector follows the IPSA voltage-layer rule. For a
  different study boundary, pass explicit `Outage` objects to `ContingencyStudy`.
