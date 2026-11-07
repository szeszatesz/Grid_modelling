"""Small explicitly constructed unit fixtures, not measurements of a real grid."""
from copy import deepcopy
import numpy as np
import pandas as pd
import pandapower as pp
import pytest
from contingency_analysis_pp import ContingencyStudy, StudyConfig, make_outages
from contingency_analysis_pp.scaling import apply_generator_scaling
from contingency_analysis_pp.capacity import _inject, _drop_sgens


def fixture_net():
    net = pp.create_empty_network()
    a = pp.create_bus(net, 110, name='A')
    b = pp.create_bus(net, 110, name='B')
    pp.create_ext_grid(net, a)
    for name in ('L1', 'L2'):
        pp.create_line_from_parameters(net, a, b, 1., .05, .2, 0., 2., name=name)
    pp.create_load(net, b, 60., 5., name='ordinary load')
    for table, creator in [('gen', pp.create_gen), ('sgen', pp.create_sgen)]:
        for technology, p in [('SOLARPHOTOVO', 10.), ('NUCLEAR', 12.), ('WINDONSHORE', 8.)]:
            idx = creator(net, b, p, name=f'{table}_{technology}', type=technology)
            net[table].at[idx, 'technology'] = technology
            net[table].at[idx, 'installed_p_mw'] = p * 2.
    for technology in ('BATTERYSTRG', 'BESS', 'AKKUMULÁTOR'):
        i = pp.create_load(net, b, 5., 1., name=f'charging_{technology}', type=technology)
        net.load.at[i, 'technology'] = technology
        net.load.at[i, 'rated_power_mw'] = 10.
    i = pp.create_sgen(net, b, 4., 1., type='BATTERYSTRG', name='discharging')
    net.sgen.at[i, 'rated_power_mw'] = 10.
    i = pp.create_gen(net, b, 3., type='BATTERYSTRG', name='battery_gen')
    net.gen.at[i, 'rated_power_mw'] = 10.
    pp.create_storage(net, b, 2., 5., q_mvar=1., name='native storage', type='other')
    return net, b


def uniform_rules(factor):
    return {'default': {'summer': factor, 'winter': factor},
            'battery': {'summer': 0., 'winter': 0.}}


@pytest.mark.parametrize('factor', [.2, .4])
def test_uniform_setpoints_and_batteries(factor):
    net, _ = fixture_net()
    before = deepcopy(net)
    audit = apply_generator_scaling(net, 'summer', uniform_rules(factor))
    for table in ('gen', 'sgen'):
        regular = ~net[table]['type'].eq('BATTERYSTRG')
        assert np.allclose(net[table].loc[regular, 'p_mw'] * net[table].loc[regular, 'scaling'],
                           before[table].loc[regular, 'p_mw'] * factor)
    batteries = audit[audit.is_battery]
    assert (batteries.p_set_after_mw == 0).all()
    assert len(batteries) == 6
    assert (net.load.loc[net.load.name.str.startswith('charging'), 'scaling'] == 0).all()
    assert net.load.at[0, 'scaling'] == 1.
    assert net.gen.loc[net.gen.name.eq('battery_gen'), 'in_service'].eq(False).all()
    pp.runpp(net)
    for element in ('load', 'sgen', 'gen', 'storage'):
        selected = batteries[batteries.element_type.eq(element)]
        assert np.allclose(net[f'res_{element}'].loc[selected.element_index, 'p_mw'], 0)
        assert np.allclose(net[f'res_{element}'].loc[selected.element_index, 'q_mvar'], 0)


@pytest.mark.parametrize('factor', [.2, .4])
def test_installed_basis_uses_mw_not_original_dispatch(factor):
    net, _ = fixture_net()
    audit = apply_generator_scaling(net, 'summer', uniform_rules(factor), basis='installed')
    regular = audit[~audit.is_battery]
    assert np.allclose(regular.utilization_installed, factor)
    assert np.allclose(regular.p_set_after_mw, regular.p_installed_mw * factor)
    pp.runpp(net)
    for element in ('gen', 'sgen'):
        r = regular[regular.element_type.eq(element)]
        assert np.allclose(net[f'res_{element}'].loc[r.element_index, 'p_mw'], r.p_set_after_mw)


def test_missing_installed_mw_fails_atomically_and_never_uses_mva():
    net, _ = fixture_net()
    pp.create_sgen(net, 1, 1., sn_mva=100., name='no MW rating', type='solar')
    saved = {n: net[n].copy(deep=True) for n in ('gen', 'sgen', 'load', 'storage')}
    with pytest.raises(ValueError, match='sn_mva is not an MW rating'):
        apply_generator_scaling(net, 'summer', uniform_rules(.4), basis='installed')
    for name, frame in saved.items():
        pd.testing.assert_frame_equal(net[name], frame)


def test_unconfigured_technology_retains_original_scaling():
    net, _ = fixture_net()
    idx = net.gen.index[net.gen.type.eq('NUCLEAR')][0]
    net.gen.at[idx, 'scaling'] = .37
    audit = apply_generator_scaling(net, 'summer', {'solar': {'summer': .2}})
    assert net.gen.at[idx, 'scaling'] == .37
    assert audit.loc[(audit.element_type == 'gen') & (audit.element_index == idx), 'status'].item() == 'unconfigured_preserved'


def test_blank_technology_falls_back_to_type_per_row():
    net, _ = fixture_net()
    net.sgen['technology'] = None
    audit = apply_generator_scaling(net, 'summer', {'solar': {'summer': .2}, 'battery': {'summer': 0}})
    assert net.sgen.loc[net.sgen.type.eq('SOLARPHOTOVO'), 'scaling'].item() == .2
    assert audit[audit.is_battery].p_set_after_mw.eq(0).all()


def test_unknown_season_preserves_unconfigured_scaling():
    net, _ = fixture_net()
    net.gen['scaling'] = .7
    apply_generator_scaling(net, 'winter', {'solar': {'summer': .2}})
    assert net.gen.scaling.eq(.7).all()


def test_battery_priority_over_other_tags():
    net, _ = fixture_net()
    idx = net.load.index[net.load.type.eq('BATTERYSTRG')][0]
    net.load.at[idx, 'technology'] = ['SOLARPHOTOVO', 'BATTERYSTRG']
    apply_generator_scaling(net, 'summer', {'solar': {'summer': 1}, 'battery': {'summer': 0}})
    assert net.load.at[idx, 'scaling'] == 0


@pytest.mark.parametrize('factor', [-.2, float('nan'), float('inf')])
def test_invalid_factors_fail_without_writes(factor):
    net, _ = fixture_net()
    saved = net.gen.copy(deep=True)
    with pytest.raises(ValueError, match='finite and nonnegative'):
        apply_generator_scaling(net, 'summer', uniform_rules(factor))
    pd.testing.assert_frame_equal(saved, net.gen)


def test_preexisting_scaling_and_utilization_are_not_confused():
    net, _ = fixture_net()
    net.gen.loc[net.gen.type.eq('SOLARPHOTOVO'), 'scaling'] = .5
    audit = apply_generator_scaling(net, 'summer', uniform_rules(.2))
    r = audit[(audit.element_type == 'gen') & ~audit.is_battery & audit.technology.str.contains('solarphotovo')].iloc[0]
    assert r.scaling_after == .2
    assert r.setpoint_ratio_after_before == pytest.approx(.4)
    assert r.utilization_installed == pytest.approx(.1)


def test_season_is_repeatable_and_zero_load_p_does_not_divide():
    net, b = fixture_net()
    study = ContingencyStudy(net, b, [], outage_elements=[], config=StudyConfig(
        generator_scales=uniform_rules(.4), load_scales={'summer': {'P': 0., 'Q': .5}}))
    study._set_season('summer')
    saved = {n: net[n].copy(deep=True) for n in ('gen', 'sgen', 'load', 'storage')}
    assert net.load.at[0, 'p_mw'] == 0
    assert net.load.at[0, 'q_mvar'] == 2.5
    study._set_season('summer')
    for n, frame in saved.items():
        pd.testing.assert_frame_equal(net[n], frame)


def test_full_study_reports_settings_audit_and_restores_storage(tmp_path):
    net, b = fixture_net()
    saved = {n: net[n].copy(deep=True) for n in ('load', 'gen', 'sgen', 'storage', 'line')}
    config = StudyConfig(generator_scales=uniform_rules(.4), max_loading_percent=10000,
                         voltage_change_limit_pu=1., default_generator_scale=.4)
    study = ContingencyStudy(net, b, [], outage_elements=make_outages(net, 'line'), config=config)
    output = study.run(output_dir=tmp_path)
    for n, frame in saved.items():
        pd.testing.assert_frame_equal(net[n], frame)
    for result in output.values():
        with pd.ExcelFile(result['report']) as workbook:
            assert 'Scaling audit' in workbook.sheet_names
            assert 'Grid' in workbook.sheet_names
            audit = pd.read_excel(workbook, sheet_name='Scaling audit')
            assert audit.loc[audit.is_battery, 'p_set_after_mw'].eq(0).all()
            params = pd.read_excel(workbook, sheet_name='Parameters').to_string()
            assert 'generator_scaling_basis' in params
            assert 'default_generator_scale' in params


def test_injection_and_existing_elements_share_alias_rules():
    net, b = fixture_net()
    created = _inject(net, b, [{'name': 'new', 'technology': 'SOLARPHOTOVO', 'P_MW': 10}],
                      1., 'summer', {'solar': {'summer': .2}})
    assert net.sgen.loc[created, 'p_mw'].item() == 2
    _drop_sgens(net, created)


def test_disabling_existing_scaling_preserves_batteries_and_dispatch():
    net, b = fixture_net()
    study = ContingencyStudy(net, b, [], outage_elements=[], config=StudyConfig(scale_existing_generators=False))
    study._set_season('summer')
    assert net.load.loc[net.load.type.eq('BATTERYSTRG'), 'scaling'].item() == 1
    assert study.scaling_audit is None


def test_power_summary_includes_native_storage_charging():
    from model_power_summary import collect_model_power_tables
    net, _ = fixture_net()
    tables = collect_model_power_tables(net)
    elements = tables['battery_by_element']
    assert elements.loc['storage', 'p_op_mw'] == -2.
    assert elements.loc['storage', 'p_charge_mw'] == 2.
    apply_generator_scaling(net, 'summer', uniform_rules(.4))
    tables = collect_model_power_tables(net)
    assert tables['totals'].loc['battery', 'p_op_mw'] == 0.
    assert tables['battery_by_element'].loc['storage', 'p_op_mw'] == 0.


def test_slack_difference_is_reported_not_overwritten():
    from contingency_analysis_pp.scaling import attach_actual_results
    net = pp.create_empty_network()
    b = pp.create_bus(net, 110, name='reference')
    i = pp.create_gen(net, b, 10., vm_pu=1., slack=True, type='GAS', name='balancer')
    net.gen.at[i, 'installed_p_mw'] = 100.
    pp.create_load(net, b, 30., 2.)
    audit = apply_generator_scaling(net, 'summer', {'default': {'summer': .2}})
    pp.runpp(net)
    result = attach_actual_results(net, audit, converged=True)
    assert result.iloc[0].p_set_after_mw == 2.
    assert result.iloc[0].actual_p_mw == pytest.approx(30.)
    assert result.iloc[0].actual_status == 'balancing_difference'


def test_study_failure_restores_all_modified_tables(tmp_path, monkeypatch):
    import contingency_analysis_pp.study as module
    net, b = fixture_net()
    saved = {n: net[n].copy(deep=True) for n in ('gen', 'sgen', 'load', 'storage', 'line')}
    def fail(*args, **kwargs):
        raise RuntimeError('forced N-1 test failure')
    monkeypatch.setattr(module, 'run_contingencies', fail)
    study = ContingencyStudy(net, b, [], outage_elements=[], config=StudyConfig(generator_scales=uniform_rules(.4)))
    with pytest.raises(RuntimeError, match='forced N-1'):
        study.run(output_dir=tmp_path)
    for name, frame in saved.items():
        pd.testing.assert_frame_equal(net[name], frame)
