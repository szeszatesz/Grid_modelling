"""Seasonal dispatch rules; MW ratings are never inferred from MVA."""
from __future__ import annotations

import re
import unicodedata
import numpy as np
import pandas as pd

TECH_COLUMNS = ('technology', 'Technológia', 'tech', 'type')
RATING_COLUMNS = ('installed_p_mw', 'p_installed_mw', 'rated_power_mw',
                  'rated_p_mw', 'p_rated_mw', 'p_nom_mw', 'pn_mw', 'max_p_mw')
ALIASES = {
    'solar': {'solar', 'solarphotovo', 'rooftoppv', 'otherres'},
    'wind': {'wind', 'windonshore', 'windoffshore'},
    'battery': {'battery', 'batterystrg', 'batterystorage', 'bess',
                'akkumulator', 'akkumulatorosenergiatarolo'},
}
AUDIT_COLUMNS = ['season', 'element_type', 'element_index', 'element_name',
    'technology', 'is_battery', 'rule', 'requested_factor', 'basis',
    'p_input_before_mw', 'scaling_before', 'p_set_before_mw', 'p_installed_mw',
    'rating_column', 'p_input_after_mw', 'scaling_after', 'p_set_after_mw',
    'expected_p_set_after_mw', 'p_error_mw', 'setpoint_ratio_after_before',
    'utilization_installed', 'in_service_before', 'in_service_after',
    'bus_in_service', 'slack', 'slack_weight', 'status']


def key(value):
    text = unicodedata.normalize('NFKD', str(value).casefold())
    return ''.join(c for c in text if c.isalnum())


def tokens(value):
    if isinstance(value, (list, tuple, set, np.ndarray)):
        return {v for item in value for v in tokens(item)}
    if value is None or bool(pd.isna(value)):
        return set()
    return {key(v) for v in re.split(r'[|;,]', str(value)) if key(v)}


def labels(row):
    return set().union(*(tokens(row.get(c)) for c in TECH_COLUMNS))


def is_battery(row, element_type=''):
    return element_type == 'storage' or bool(labels(row) & ALIASES['battery'])


def battery_mask(frame, element_type=''):
    if frame.empty:
        return pd.Series(False, index=frame.index, dtype=bool)
    return frame.apply(lambda row: is_battery(row, element_type), axis=1).astype(bool)


def resolve_rule(value, season, rules, *, battery=False):
    values = tokens(value)
    if battery or values & ALIASES['battery']:
        values = ALIASES['battery']
        battery = True
    normalized = [(str(name), key(name), seasonal) for name, seasonal in rules.items()]
    for name, normalized_name, seasonal in normalized:
        if normalized_name in values and (not battery or normalized_name in ALIASES['battery']):
            if season in seasonal:
                return name, validate_factor(seasonal[season])
    for name, normalized_name, seasonal in normalized:
        group = ALIASES.get(normalized_name, {normalized_name})
        if values & group and (not battery or group & ALIASES['battery']):
            if season in seasonal:
                return name, validate_factor(seasonal[season])
    for name, normalized_name, seasonal in normalized:
        if normalized_name in {'default', 'all'} and season in seasonal:
            return name, validate_factor(seasonal[season])
    return None, None


def seasonal_factor(value, season, rules):
    _, factor = resolve_rule(value, season, rules)
    return 1.0 if factor is None else factor


def validate_factor(value):
    factor = float(value)
    if not np.isfinite(factor) or factor < 0:
        raise ValueError(f'Seasonal scaling must be finite and nonnegative, got {value!r}')
    return factor


def rating(row, columns=RATING_COLUMNS):
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


def apply_generator_scaling(net, season, rules, *, basis='setpoint',
                            rating_columns=RATING_COLUMNS,
                            zero_battery_disconnect_gen=True):
    """Change dispatch only. Caller restores baseline tables between seasons.

    setpoint: factor multiplies baseline p_mw (replaces baseline scaling).
    installed: factor multiplies an explicit positive MW rating. Missing ratings
    fail before ANY scaling writes, except that factor zero needs no rating.
    Charging direction is preserved. Ordinary loads are not changed here.
    """
    if basis not in {'setpoint', 'installed'}:
        raise ValueError("generator_scaling_basis must be 'setpoint' or 'installed'")
    plans = []
    for element in ('gen', 'sgen', 'load', 'storage'):
        frame = net.get(element)
        if frame is None:
            continue
        for idx, row in frame.iterrows():
            battery = is_battery(row, element)
            if element == 'load' and not battery:
                continue
            values = labels(row)
            name, factor = resolve_rule(list(values), season, rules, battery=battery)
            before_p = _float(row, 'p_mw')
            before_scale = _float(row, 'scaling', 1.)
            installed, column = rating(row, rating_columns)
            after_p, after_scale = before_p, before_scale
            on_before = bool(row.get('in_service', True))
            on_after = on_before
            status = 'unconfigured_preserved'
            if factor is not None:
                after_scale = factor
                status = 'scaled'
                if basis == 'installed' and factor != 0:
                    if not np.isfinite(installed):
                        raise ValueError(f'Installed-MW scaling requires a positive MW rating: '
                                         f'{element}[{idx!r}] {row.get("name", "")!r}. '
                                         f'Provide one of {tuple(rating_columns)!r}; '
                                         'sn_mva is not an MW rating.')
                    direction = -1. if before_p < 0 else 1.
                    after_p = direction * installed
                if battery and factor == 0 and element == 'gen' and zero_battery_disconnect_gen:
                    on_after = False
                    status = 'disabled_zero_battery_gen'
            sign = -1. if element in {'load', 'storage'} else 1.
            bus = row.get('bus')
            bus_on = bool(net.bus.at[bus, 'in_service']) if bus in net.bus.index else False
            before_op = sign * before_p * before_scale if on_before and bus_on else 0.
            after_op = sign * after_p * after_scale if on_after and bus_on else 0.
            slack = bool(row.get('slack', False)) if element == 'gen' else False
            weight = _float(row, 'slack_weight', 0.) if element == 'gen' else 0.
            if element == 'gen' and (slack or weight > 0) and on_after:
                status += ';balancing_actual_P_may_differ'
            plans.append((element, idx, after_p, after_scale, on_after, {
                'season': season, 'element_type': element, 'element_index': idx,
                'element_name': row.get('name'), 'technology': ' | '.join(sorted(values)),
                'is_battery': battery, 'rule': name, 'requested_factor': factor,
                'basis': basis, 'p_input_before_mw': before_p,
                'scaling_before': before_scale, 'p_set_before_mw': before_op,
                'p_installed_mw': installed, 'rating_column': column,
                'p_input_after_mw': after_p, 'scaling_after': after_scale,
                'p_set_after_mw': after_op, 'expected_p_set_after_mw': after_op,
                'p_error_mw': 0.,
                'setpoint_ratio_after_before': after_op / before_op if before_op else np.nan,
                'utilization_installed': after_op / installed if np.isfinite(installed) else np.nan,
                'in_service_before': on_before, 'in_service_after': on_after,
                'bus_in_service': bus_on, 'slack': slack, 'slack_weight': weight,
                'status': status,
            }))
    for element, idx, after_p, after_scale, on_after, record in plans:
        if record['requested_factor'] is None:
            continue
        net[element].at[idx, 'p_mw'] = after_p
        net[element].at[idx, 'scaling'] = after_scale
        net[element].at[idx, 'in_service'] = on_after
    for element, idx, _, _, _, record in plans:
        row = net[element].loc[idx]
        sign = -1. if element in {'load', 'storage'} else 1.
        actual_setpoint = (sign * float(row['p_mw']) * float(row.get('scaling', 1.))
                           if bool(row.get('in_service', True)) and record['bus_in_service'] else 0.)
        record['p_set_after_mw'] = actual_setpoint
        record['p_error_mw'] = actual_setpoint - record['expected_p_set_after_mw']
    if plans:
        net['converged'] = False  # Previous res_* tables no longer describe this dispatch.
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
        [audit.actual_p_mw.isna(),
         audit.actual_minus_setpoint_mw.abs().le(tolerance_mw),
         audit.slack | audit.slack_weight.gt(0)],
        ['unavailable_or_unsupplied', 'matches_setpoint', 'balancing_difference'],
        default='investigate_difference',
    )
    return audit
