"""Estimation: first stage, IV specifications, conflict-active heterogeneity,
event-type decompositions, and the expanding-window estimate series.

Ported from analysis/wind-IV.ipynb cells 8, 9, 12, 13 and 20 (the
partially-instrumented specifications reported in the paper's main tables).
"""
import numpy as np
import pandas as pd
import pyfixest as pf

from . import config

FE = 'district + year_month'
VCOV = {'CRV1': 'province'}

LAG_FRP = [f'log_frp_l{l}' for l in range(1, config.N_LAGS + 1)]
LAG_UPWIND = [f'log_upwind_frp_l{l}' for l in range(1, config.N_LAGS + 1)]
CONTROLS = ' + '.join(LAG_FRP) if LAG_FRP else '1'
IV_TERMS = ' + '.join(['log_upwind_frp'] + LAG_UPWIND)
FS_FML = f'log_frp ~ {IV_TERMS} | {FE}'


def _coef_row(res, coef='log_frp'):
    tidy = res.tidy().reset_index()
    row = tidy[tidy['Coefficient'] == coef].iloc[0]
    return {'coef': float(row['Estimate']), 'se': float(row['Std. Error']),
            'p': float(row['Pr(>|t|)'])}


def first_stage(panel):
    res = pf.feols(FS_FML, data=panel, vcov=VCOV)
    r2w, n = res._r2_within, res._N
    k = len(LAG_UPWIND) + 1
    f_stat = (r2w / k) / ((1 - r2w) / (n - k - 1))
    out = {'f_stat': float(f_stat), 'r2_within': float(r2w), 'n': int(n)}
    out['contemporaneous'] = _coef_row(res, 'log_upwind_frp')
    if LAG_UPWIND:
        out['lag1'] = _coef_row(res, LAG_UPWIND[0])
    return out


def _iv(panel, outcome, distributed_lag=True):
    if distributed_lag:
        fml = f'{outcome} ~ {CONTROLS} | {FE} | log_frp ~ {IV_TERMS}'
    else:
        fml = f'{outcome} ~ 1 | {FE} | log_frp ~ log_upwind_frp'
    res = pf.feols(fml, data=panel, vcov=VCOV)
    return res


def full_sample_iv(panel):
    specs = [
        ('IV-1', 'events', True), ('IV-2', 'pv_events', True),
        ('IV-3', 'events', False), ('IV-4', 'pv_events', False),
    ]
    rows = []
    for label, outcome, dl in specs:
        res = _iv(panel, outcome, dl)
        row = {'label': label, 'outcome': outcome,
               'distributed_lag': dl, 'n': int(res._N), **_coef_row(res)}
        rows.append(row)
    return rows


def active_subsample(panel, thresh):
    """Districts with conflict events in >= thresh share of panel months."""
    active_months = panel.groupby('district')['events'].apply(lambda s: (s > 0).sum())
    n_months = panel['year_month'].nunique()
    keep = active_months[active_months >= thresh * n_months].index
    return panel[panel['district'].isin(keep)]


def conflict_active_table(panel):
    rows = []
    for thresh in config.ACTIVE_THRESHOLDS:
        sub = active_subsample(panel, thresh)
        fs = pf.feols(FS_FML, data=sub, vcov=VCOV)
        r2w, n, k = fs._r2_within, fs._N, len(LAG_UPWIND) + 1
        sub_f = (r2w / k) / ((1 - r2w) / (n - k - 1))
        row = {'threshold': thresh,
               'n_districts': int(sub['district'].nunique()),
               'n_obs': int(len(sub)),
               'zero_share': float((sub['events'] == 0).mean()),
               'first_stage_F': float(sub_f)}
        for outcome in ['events', 'pv_events']:
            row[outcome] = _coef_row(_iv(sub, outcome))
        rows.append(row)
    return rows


def event_type_tables(panel):
    sub30 = active_subsample(panel, 0.30)
    twoway = {}
    for col, label in [('riots_protests', 'Riots/Protests'),
                       ('battles_violence', 'Battles/Violence')]:
        twoway[col] = {'label': label, **_coef_row(_iv(sub30, col))}
    fourway = {}
    for col, label in config.FOURWAY_TYPES.items():
        fourway[col] = {'label': label, **_coef_row(_iv(sub30, col))}
    return twoway, fourway


def expanding_window_series(panel, min_end_ym=201912):
    """Headline tau>=30% coefficient re-estimated on expanding windows.

    Conflict-activity shares are recomputed inside each window (no
    lookahead), matching how an out-of-sample update would have run at the
    time. Semi-annual points until the last 18 months, then monthly.
    """
    all_yms = sorted(panel['year_month'].unique())
    recent = all_yms[-18:] if len(all_yms) > 18 else all_yms
    ends = [ym for ym in all_yms
            if ym >= min_end_ym and (ym % 100 in (6, 12) or ym in recent)]
    rows = []
    for end in ends:
        win = panel[panel['year_month'] <= end]
        sub = active_subsample(win, 0.30)
        try:
            point = {'end_ym': int(end),
                     'n_districts': int(sub['district'].nunique())}
            for outcome in ['events', 'pv_events']:
                point[outcome] = _coef_row(_iv(sub, outcome))
            rows.append(point)
        except Exception as exc:
            print(f'  expanding window {end}: skipped ({exc})')
    return rows


def national_series(panel):
    agg = (panel.groupby('year_month')
           .agg(events=('events', 'sum'), pv_events=('pv_events', 'sum'),
                riots=('riots', 'sum'), protests=('protests', 'sum'),
                violence_against_civilians=('violence_against_civilians', 'sum'),
                total_frp=('total_frp', 'sum'),
                mean_log_upwind=('log_upwind_frp', 'mean'))
           .reset_index())
    agg['total_frp'] = agg['total_frp'].round(1)
    agg['mean_log_upwind'] = agg['mean_log_upwind'].round(4)
    return agg.to_dict(orient='records')


def district_latest(panel, k_months=3):
    """Per-district sums over the last k complete months, for the map."""
    last = sorted(panel['year_month'].unique())[-k_months:]
    sub = panel[panel['year_month'].isin(last)]
    agg = (sub.groupby('district')
           .agg(events=('events', 'sum'), pv_events=('pv_events', 'sum'),
                total_frp=('total_frp', 'sum'),
                log_upwind=('log_upwind_frp', 'mean'))
           .reset_index())
    agg['total_frp'] = agg['total_frp'].round(1)
    agg['log_upwind'] = agg['log_upwind'].round(3)
    return {'months': [int(m) for m in last],
            'districts': agg.to_dict(orient='records')}
