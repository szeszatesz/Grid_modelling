"""Seasonal dispatch for balanced gen/sgen/load/storage elements.

Basis semantics (non-fixed, non-battery gen/sgen):
  installed : p_mw := explicit installed MW, scaling := study factor
              -> P = installed * factor (any model factor is OVERRIDDEN).
              Rows without installed MW keep p_mw (audited fallback).
  setpoint  : scaling := model scaling * study factor, p_mw unchanged
              -> P = p_mw * model_factor * study_factor (CHAINED).
  builder   : pp_builder selects absolute factors, p_mw unchanged.
Fixed-setpoint generators (conventional, biogas, hydro, standalone OTHERRES)
are never touched. Batteries follow explicit rules. sn_mva is never MW.
Solar rules may be keyed by builder portfolio (HMKE, MEKH_0_5_TO_5,
MEKH_ABOVE_5, BTM_MV, DISTRIBUTED_22KV); a portfolio rule beats generic ones.
"""
from __future__ import annotations

import ast
from collections.abc import Mapping
from contextlib import redirect_stdout
from io import StringIO
import re
import unicodedata
import warnings
import numpy as np
import pandas as pd

RATING_COLUMNS = ('installed_capacity_mw', 'installed_p_mw', 'p_installed_mw',
                  'rated_power_mw', 'rated_p_mw', 'p_rated_mw', 'p_nom_mw',
                  'pn_mw', 'max_p_mw')
TECH_COLUMNS = ('technology', 'Technológia', 'tech', 'type')
SOLAR_PORTFOLIOS = ('HMKE', 'MEKH_0_5_TO_5', 'MEKH_ABOVE_5', 'BTM_MV', 'DISTRIBUTED_22KV')
# Same values as pp_builder.config.SOLAR_SCALING_DEFAULTS (summer, winter).
DEFAULT_SOLAR_PORTFOLIO_SCALES = {
    'HMKE': {'summer': 0.1, 'winter': 0.20},
    'MEKH_0_5_TO_5': {'summer': 0.2, 'winter': 0.30},
    'MEKH_ABOVE_5': {'summer': 0.3, 'winter': 0.30},
    'BTM_MV': {'summer': 0.4, 'winter': 0.3},
    'DISTRIBUTED_22KV': {'summer': 0.5, 'winter': 0.3},
}
ALIASES = {
    'solar': {'solar', 'solarphotovo', 'rooftoppv', 'otherres', 'pv'},
    'wind': {'wind', 'windonshore', 'windoffshore'},
    'battery': {'battery', 'batterystrg', 'batterystorage', 'bess',
                'akkumulator', 'akkumulatorosenergiatarolo'},
    'hydro': {'hydro', 'hydroranpond', 'hydroreserv'},
}
FIXED_SETPOINT_TECHNOLOGIES = frozenset({
    'nuclear', 'hardcoalbf', 'hardcoalold1', 'lightoil', 'othernonres',
    'gasccgtnew', 'gasccgtold2', 'gasccgtpres1', 'gasccgtpres2',
    'gasconvold1', 'gasocgtnew', 'gasbio', 'hydroranpond', 'hydroreserv',
    'otherres'})
SCALABLE_MARKERS = frozenset({'solarphotovo', 'rooftoppv', 'solar', 'pv',
                              'windonshore', 'windoffshore', 'wind'})
AUDIT_COLUMNS = ['season', 'element_type', 'element_index', 'element_name',
    'technology', 'solar_scaling_group', 'is_battery', 'is_fixed_setpoint',
    'rule', 'requested_factor', 'basis', 'p_input_before_mw', 'scaling_before',
    'p_set_before_mw', 'p_installed_mw', 'rating_column', 'p_input_after_mw',
    'scaling_after', 'p_set_after_mw', 'expected_p_set_after_mw', 'p_error_mw',
    'setpoint_ratio_after_before', 'utilization_installed', 'in_service_before',
    'in_service_after', 'bus_in_service', 'slack', 'slack_weight', 'status',
    'effective_basis', 'reference_p_mw', 'reference_source', 'rating_source',
    'rating_is_legacy_proxy', 'missing_rating_fallback',
    'q_set_before_mvar', 'q_set_after_mvar']


def key(value):
    text = unicodedata.normalize('NFKD', str(value).casefold())
    return ''.join(c for c in text if c.isalnum())


def tokens(value):
    if isinstance(value, (list, tuple, set, np.ndarray)):
        return {v for item in value for v in tokens(item)}
    if value is None or value is pd.NA or bool(pd.isna(value)):
        return set()
    text = str(value).strip()
    if text.startswith(('(', '[')):
        try:
            parsed = ast.literal_eval(text)
            if isinstance(parsed, (list, tuple, set)):
                return tokens(parsed)
        except (ValueError, SyntaxError):
            pass
    return {key(v) for v in re.split(r'[|;,]', text) if key(v)}


def labels(row):
    return set().union(*(tokens(row.get(c)) for c in TECH_COLUMNS))


def is_battery(row, element_type=''):
    return element_type == 'storage' or bool(labels(row) & ALIASES['battery'])


def is_fixed_setpoint(row, element_type='', fixed=FIXED_SETPOINT_TECHNOLOGIES):
    if element_type not in ('gen', 'sgen') or is_battery(row, element_type):
        return False
    fixed = {key(f) for f in fixed}
    found = labels(row)
    return bool(found & fixed) and not (found & SCALABLE_MARKERS)


def battery_mask(frame, element_type=''):
    if frame.empty:
        return pd.Series(False, index=frame.index, dtype=bool)
    return frame.apply(lambda row: is_battery(row, element_type), axis=1).astype(bool)


def validate_factor(value):
    try:
        factor = float(value)
    except (ValueError, TypeError) as exc:
        raise ValueError(f'Seasonal scaling must be finite and nonnegative, got {value!r}') from exc
    if not np.isfinite(factor) or factor < 0:
        raise ValueError(f'Seasonal scaling must be finite and nonnegative, got {value!r}')
    return factor


def _factor_for_season(value, season):
    if isinstance(value, Mapping):
        return validate_factor(value[season]) if season in value else None
    return validate_factor(value)


def solar_group(row):
    group = row.get('solar_scaling_group')
    if isinstance(group, str) and group.strip():
        for g in SOLAR_PORTFOLIOS:
            if key(g) == key(group):
                return g
    return None


def resolve_rule(value, season, rules, *, battery=False, group=None):
    """Portfolio rule > exact label > family alias > default/all."""
    if group is not None and not battery:
        for name, setting in rules.items():
            if key(name) == key(group):
                factor = _factor_for_season(setting, season)
                if factor is not None:
                    return str(name), factor
    values = tokens(value)
    battery = battery or bool(values & ALIASES['battery'])
    if battery:
        values = ALIASES['battery']
    normalized = [(str(name), tokens(name), setting) for name, setting in rules.items()
                  if key(name) not in {key(g) for g in SOLAR_PORTFOLIOS}]
    for alias_pass in (False, True):
        for name, names, setting in normalized:
            if names & {'default', 'all'}:
                continue
            grp = set().union(*(ALIASES.get(n, {n}) for n in names)) if alias_pass else names
            if values & grp and (not battery or grp & ALIASES['battery']):
                factor = _factor_for_season(setting, season)
                if factor is not None:
                    return name, factor
    for name, names, setting in normalized:
        if names & {'default', 'all'}:
            factor = _factor_for_season(setting, season)
            if factor is not None:
                return name, factor
    return None, None


def seasonal_factor(value, season, rules):
    _, factor = resolve_rule(value, season, rules)
    return 1.0 if factor is None else factor


def rating(row, columns=RATING_COLUMNS):
    columns = tuple(dict.fromkeys(('installed_capacity_mw', *columns)))
    for column in columns:
        try:
            value = float(row.get(column, np.nan))
        except (TypeError, ValueError):
            continue
        if np.isfinite(value) and value > 0:
            return value, column
    return np.nan, None


def _float(row, column, default=0.):
    value = float(row.get(column, default))
    if not np.isfinite(value):
        raise ValueError(f'Non-finite {column}: {value}')
    return value


def _builder_factors(net, season, rules, fixed):
    try:
        from pp_builder.generator_builder import apply_gen_scaling
        from pp_builder.config import SOLAR_SCALING_DEFAULTS
    except ImportError as exc:
        raise ImportError("basis='builder' requires pp_builder on PYTHONPATH") from exc
    staged = {t: net.get(t, pd.DataFrame()).copy(deep=True) for t in ('gen', 'sgen')}
    portfolio_keys = {key(g): g for g in SOLAR_SCALING_DEFAULTS}
    solar_overrides, tech_overrides, selected = {}, {}, {}
    for name, setting in rules.items():
        group = portfolio_keys.get(key(name))
        factor = _factor_for_season(setting, season)
        if group is not None and factor is not None:
            solar_overrides[group] = factor
    for table, frame in staged.items():
        if 'technology' not in frame:
            frame['technology'] = pd.Series(index=frame.index, dtype=object)
        else:
            frame['technology'] = frame.technology.astype(object)
        for idx, row in frame.iterrows():
            if is_battery(row, table) or is_fixed_setpoint(row, table, fixed):
                continue
            tech = tokens(row.get('technology'))
            if not tech:
                for column in TECH_COLUMNS[1:]:
                    tech = tokens(row.get(column))
                    if tech:
                        break
            if not tech:
                name, factor = resolve_rule([], season, rules)
                if factor is not None:
                    selected[(table, idx)] = (name, factor)
                continue
            raw = row.get('technology')
            if not tokens(raw):
                raw = next(row.get(c) for c in TECH_COLUMNS[1:] if tokens(row.get(c)))
            if isinstance(raw, str) and raw.strip().startswith(('(', '[')):
                try:
                    raw = ast.literal_eval(raw)
                except (ValueError, SyntaxError):
                    pass
            frame.at[idx, 'technology'] = raw
            name, factor = resolve_rule(list(labels(row)), season, rules)
            if factor is not None:
                for label in (raw if isinstance(raw, (list, tuple)) else [raw]):
                    tech_overrides[label] = factor
            selected[(table, idx)] = (name or 'builder_defaults', None)
    with redirect_stdout(StringIO()):
        apply_gen_scaling(staged, season, tech_overrides=tech_overrides,
                          solar_overrides=solar_overrides or None)
    factors = {}
    for (table, idx), (name, direct) in selected.items():
        row = staged[table].loc[idx]
        factor = direct if direct is not None else validate_factor(row.get('scaling', 1.))
        group = row.get('solar_scaling_group')
        if isinstance(group, str) and group in SOLAR_SCALING_DEFAULTS:
            name = 'builder_portfolio:' + group
        factors[(table, idx)] = (name, factor)
    return factors


def apply_generator_scaling(net, season, rules, *, basis='setpoint',
                            rating_columns=RATING_COLUMNS,
                            zero_battery_disconnect_gen=True,
                            missing_rating_policy='setpoint',
                            default_generator_scale=None,
                            fixed_setpoint_technologies=FIXED_SETPOINT_TECHNOLOGIES):
    """Validate every proposal first, then write. See module docstring.

    setpoint is CHAINED (model scaling x study factor), so call it once per
    restored baseline; the study restores tables between seasons.
    """
    if basis not in {'installed', 'setpoint', 'builder'}:
        raise ValueError("generator_scaling_basis must be 'installed', 'setpoint' or 'builder'")
    if missing_rating_policy not in {'setpoint', 'raise'}:
        raise ValueError("missing_rating_policy must be 'setpoint' or 'raise'")
    fixed_set = frozenset(key(f) for f in (fixed_setpoint_technologies or ()))
    rules = dict(rules)
    if default_generator_scale is not None:
        rules['default'] = {season: validate_factor(default_generator_scale)}
    builder = _builder_factors(net, season, rules, fixed_set) if basis == 'builder' else {}
    plans = []
    for element in ('gen', 'sgen', 'load', 'storage'):
        frame = net.get(element)
        if frame is None:
            continue
        for idx, row in frame.iterrows():
            battery = is_battery(row, element)
            if element == 'load' and not battery:
                continue
            fixed = is_fixed_setpoint(row, element, fixed_set)
            values = labels(row)
            group = None if (fixed or battery) else solar_group(row)
            name, factor = (None, None) if fixed else resolve_rule(
                list(values), season, rules, battery=battery, group=group)
            if basis == 'builder' and not battery and not fixed:
                name, factor = builder.get((element, idx), (None, None))
            before_p = _float(row, 'p_mw')
            before_scale = _float(row, 'scaling', 1.)
            installed, column = rating(row, rating_columns)
            after_p, after_scale = before_p, before_scale
            on_before = bool(row.get('in_service', True))
            on_after = on_before
            effective = 'fixed_setpoint' if fixed else 'preserved'
            reference, reference_source = abs(before_p), 'unscaled_p_mw'
            fallback = False
            status = 'fixed_setpoint_preserved' if fixed else 'unconfigured_preserved'
            if factor is not None:
                status = 'scaled'
                chained = basis == 'setpoint' and not battery
                after_scale = before_scale * factor if chained else factor
                effective = ('chained_setpoint' if chained else
                             'builder_setpoint' if basis == 'builder' else 'setpoint')
                if basis == 'installed':
                    if np.isfinite(installed):
                        after_p = (-1. if before_p < 0 else 1.) * installed
                        effective, reference, reference_source = 'installed', installed, column
                    elif factor == 0:
                        effective = 'zero_without_rating'
                    elif missing_rating_policy == 'raise':
                        raise ValueError(f'Installed-MW scaling requires a positive MW rating: '
                            f'{element}[{idx!r}] {row.get("name", "")!r}. '
                            "sn_mva is not an MW rating. Supply MW metadata or use "
                            "missing_rating_policy='setpoint'.")
                    else:
                        effective, fallback = 'setpoint_fallback', True
                        status = 'scaled;missing_rating_setpoint_fallback'
                if battery and factor == 0 and element == 'gen' and zero_battery_disconnect_gen:
                    on_after = False
                    status += ';disabled_zero_battery_gen'
            sign = -1. if element in {'load', 'storage'} else 1.
            bus = row.get('bus')
            bus_on = bool(net.bus.at[bus, 'in_service']) if bus in net.bus.index else False
            before_op = sign * before_p * before_scale if on_before and bus_on else 0.
            after_op = sign * after_p * after_scale if on_after and bus_on else 0.
            slack = bool(row.get('slack', False)) if element == 'gen' else False
            weight = _float(row, 'slack_weight', 0.) if element == 'gen' else 0.
            if element == 'gen' and (slack or weight > 0) and on_after:
                status += ';balancing_actual_P_may_differ'
            q_before = q_after = np.nan
            if element != 'gen':
                q = _float(row, 'q_mvar')
                q_before = sign * q * before_scale if on_before and bus_on else 0.
                q_after = sign * q * after_scale if on_after and bus_on else 0.
            record = dict.fromkeys(AUDIT_COLUMNS)
            record.update(
                season=season, element_type=element, element_index=idx,
                element_name=row.get('name'), technology=' | '.join(sorted(values)),
                solar_scaling_group=group, is_battery=battery, is_fixed_setpoint=fixed,
                rule=name, requested_factor=factor, basis=basis,
                p_input_before_mw=before_p, scaling_before=before_scale,
                p_set_before_mw=before_op, p_installed_mw=installed, rating_column=column,
                p_input_after_mw=after_p, scaling_after=after_scale,
                p_set_after_mw=after_op, expected_p_set_after_mw=after_op, p_error_mw=0.,
                setpoint_ratio_after_before=after_op / before_op if before_op else np.nan,
                utilization_installed=after_op / installed if np.isfinite(installed) else np.nan,
                in_service_before=on_before, in_service_after=on_after, bus_in_service=bus_on,
                slack=slack, slack_weight=weight, status=status, effective_basis=effective,
                reference_p_mw=reference, reference_source=reference_source,
                rating_source='explicit_mw_column' if column is not None else 'missing',
                rating_is_legacy_proxy=False, missing_rating_fallback=fallback,
                q_set_before_mvar=q_before, q_set_after_mvar=q_after)
            plans.append((element, idx, after_p, after_scale, on_after, record))
    n_fb = sum(p[-1]['missing_rating_fallback'] for p in plans)
    if n_fb:
        warnings.warn(f'{n_fb} scalable element(s) have no explicit installed MW; '
                      'p_mw is kept and only the factor is replaced. See effective_basis '
                      'in Scaling audit.', UserWarning, stacklevel=2)
    for element, idx, p, scale, on, record in plans:
        if record['requested_factor'] is not None:
            net[element].at[idx, 'p_mw'] = p
            net[element].at[idx, 'scaling'] = scale
            net[element].at[idx, 'in_service'] = on
    for element, idx, _, _, _, record in plans:
        row = net[element].loc[idx]
        sign = -1. if element in {'load', 'storage'} else 1.
        actual = (sign * float(row.p_mw) * float(row.get('scaling', 1.))
                  if bool(row.get('in_service', True)) and record['bus_in_service'] else 0.)
        record['p_set_after_mw'] = actual
        record['p_error_mw'] = actual - record['expected_p_set_after_mw']
    if any(p[-1]['requested_factor'] is not None for p in plans):
        net['converged'] = False
    return pd.DataFrame([p[-1] for p in plans], columns=AUDIT_COLUMNS)


def attach_actual_results(net, audit, *, converged, tolerance_mw=1e-7):
    """Compare scheduled and solved P without hiding slack/islanding differences."""
    if audit is None:
        return None
    audit = audit.copy()
    actual = []
    for _, row in audit.iterrows():
        element, idx = row['element_type'], row['element_index']
        if not row['in_service_after'] or not row['bus_in_service']:
            value = 0.
        else:
            frame = net.get(f'res_{element}', pd.DataFrame())
            value = (float(frame.at[idx, 'p_mw']) if converged and idx in frame.index
                     and 'p_mw' in frame else np.nan)
            if element in {'load', 'storage'}:
                value = -value
        actual.append(value)
    audit['actual_p_mw'] = actual
    audit['actual_minus_setpoint_mw'] = audit.actual_p_mw - audit.p_set_after_mw
    audit['actual_utilization_installed'] = audit.actual_p_mw / audit.p_installed_mw
    audit['results_converged'] = bool(converged)
    audit['actual_status'] = np.select(
        [audit.actual_p_mw.isna(), audit.actual_minus_setpoint_mw.abs().le(tolerance_mw),
         audit.slack | audit.slack_weight.gt(0)],
        ['unavailable_or_unsupplied', 'matches_setpoint', 'balancing_difference'],
        default='investigate_difference')
    return audit
