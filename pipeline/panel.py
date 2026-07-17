"""Assemble the balanced district-month IV panel from the stored artifacts.

Ported from analysis/wind-IV.ipynb cell 7.
"""
import numpy as np
import pandas as pd

from . import config


def build_panel(conflict_pm, fires_pm, instrument_df, district_wind, end_ym):
    """Balanced panel over all conflict-matched districts x months.

    end_ym: last (complete) year*100+month to include.
    """
    all_districts = np.sort(conflict_pm['district'].unique())
    all_ym = pd.DataFrame(
        list(config.ym_iter(config.ym(config.START_YEAR, 1), end_ym)),
        columns=['year', 'month'])

    panel = pd.merge(pd.DataFrame({'district': all_districts}), all_ym, how='cross')

    outcome_cols = [c for c in conflict_pm.columns
                    if c not in ('district', 'year', 'month')]
    panel = panel.merge(conflict_pm, on=['district', 'year', 'month'], how='left')
    panel[outcome_cols] = panel[outcome_cols].fillna(0).astype(int)

    panel = panel.merge(fires_pm[['district', 'year', 'month', 'log_frp', 'total_frp']],
                        on=['district', 'year', 'month'], how='left')
    panel[['log_frp', 'total_frp']] = panel[['log_frp', 'total_frp']].fillna(0)

    panel = panel.merge(
        instrument_df[['district', 'year', 'month', 'log_upwind_frp']],
        on=['district', 'year', 'month'], how='left')
    panel['log_upwind_frp'] = panel['log_upwind_frp'].fillna(0)

    panel = panel.merge(
        district_wind[['district', 'year', 'month', 'wind_speed', 'wind_dir_from']],
        on=['district', 'year', 'month'], how='left')

    panel = panel.sort_values(['district', 'year', 'month']).reset_index(drop=True)
    for lag in range(1, config.N_LAGS + 1):
        panel[f'log_frp_l{lag}'] = panel.groupby('district')['log_frp'].shift(lag)
        panel[f'log_upwind_frp_l{lag}'] = (
            panel.groupby('district')['log_upwind_frp'].shift(lag))

    lag_cols = ([f'log_frp_l{l}' for l in range(1, config.N_LAGS + 1)] +
                [f'log_upwind_frp_l{l}' for l in range(1, config.N_LAGS + 1)])
    panel = panel.dropna(subset=lag_cols).reset_index(drop=True)
    panel['year_month'] = panel['year'] * 100 + panel['month']
    return panel


def attach_province(panel, districts_gdf):
    prov = dict(zip(districts_gdf['district'], districts_gdf['province']))
    panel = panel.copy()
    panel['province'] = panel['district'].map(prov)
    return panel
