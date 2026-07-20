"""Weekly incremental update for the Brazil wind-IV panel, mirroring
nigeria/pipeline_update.py's structure and per-source try/except isolation
(one source's failure can't block the others or stop results_brazil.json
from being written with whatever vintages are actually available).

Unlike nigeria/pipeline_update.py, districts are loaded from the already-
committed, already-simplified districts.parquet artifact rather than
re-parsed from a raw GADM shapefile every run - Brazil's raw shapefile
(~264 MB, 5,572 municipalities) is deliberately not committed to this repo
(see config.BOOTSTRAP_SHP's docstring), so it will not exist in CI at all.

update_conflict bounds acled_through by the data actually returned, the
same min(last_complete_month(), actual_max) pattern Nigeria/Indonesia use -
critical here specifically because this is the exact bug that contaminated
the original scoping/brazil_round3.py run (see brazil/config.py's
PANEL_START/FETCH_START docstring): without this bound, a future ACLED
lag would silently get zero-filled as "confirmed no conflict" again.

Run:  python -m brazil.pipeline_update        (from the repo root)
"""
import json
from datetime import date, datetime, timezone

import geopandas as gpd
import numpy as np
import pandas as pd

from . import config as cfg
from . import acled as acled_mod
from . import fires as fires_mod
from .wind import fetch_wind_months_batched
from pipeline import spatial, estimate
from pipeline import instrument as instr
from pipeline.fetch_wind import grid_to_district_wind


def last_complete_month(today=None):
    today = today or date.today()
    y, m = (today.year, today.month - 1) if today.month > 1 else (today.year - 1, 12)
    return y * 100 + m


def _next(ym_val):
    y, m = divmod(ym_val, 100)
    return (y + 1) * 100 + 1 if m == 12 else ym_val + 1


def load_districts():
    return gpd.read_parquet(cfg.DISTRICTS_PARQUET)


def update_conflict(meta, districts):
    start_y, start_m = divmod(cfg.FETCH_START, 100)
    acled = acled_mod.fetch_acled_country('Brazil', date(start_y, start_m, 1).isoformat())
    conflict_pm = acled_mod.aggregate_conflict_with_event_types(acled, districts)
    through = min(last_complete_month(),
                  int(conflict_pm.eval('year * 100 + month').max()))
    conflict_pm = conflict_pm[conflict_pm.eval('year * 100 + month') <= through]
    conflict_pm.to_parquet(cfg.CONFLICT_PM_PARQUET, index=False)
    meta['acled_through'] = through
    print(f'  conflict artifact replaced, through {through}')
    return meta


def update_wind(meta, districts):
    start = meta['wind_through']
    want = list(cfg.ym_iter(_next(start), last_complete_month()))
    if not want:
        print('  wind: up to date')
        return meta
    grid = fetch_wind_months_batched(want)
    if grid is None or grid.empty:
        return meta
    new_dw = grid_to_district_wind(grid, districts)
    dw = pd.read_parquet(cfg.WIND_PM_PARQUET)
    got = set(new_dw.eval('year * 100 + month').unique())
    dw = pd.concat([dw[~dw.eval('year * 100 + month').isin(got)], new_dw],
                   ignore_index=True)
    dw.to_parquet(cfg.WIND_PM_PARQUET, index=False)
    meta['wind_through'] = int(dw.eval('year * 100 + month').max())
    print(f'  wind artifact extended, through {meta["wind_through"]}')
    return meta


def update_fires(meta, districts):
    refetch_from = _next(meta['fire_final_through'])
    detections, new_final = fires_mod.fetch_fires_since(refetch_from)
    if detections is None:
        return meta
    fires_pm = pd.read_parquet(cfg.FIRES_PM_PARQUET)
    instrument_df = pd.read_parquet(cfg.INSTRUMENT_PARQUET)
    fires_pm = fires_pm[fires_pm.eval('year * 100 + month') < refetch_from]
    instrument_df = instrument_df[
        instrument_df.eval('year * 100 + month') < refetch_from]

    if len(detections):
        assigned = spatial.assign_points_to_districts(
            detections.reset_index(drop=True), districts).dropna(subset=['district'])
        new_pm = spatial.aggregate_fires(assigned, districts)
        fires_pm = pd.concat([fires_pm, new_pm], ignore_index=True)

        dw = pd.read_parquet(cfg.WIND_PM_PARQUET)
        months_with_fire = set(detections.eval('year * 100 + month').unique())
        dw_new = dw[dw.eval('year * 100 + month').isin(months_with_fire)]
        if len(dw_new):
            new_instr = instr.build_instrument(detections, dw_new, districts)
            instrument_df = pd.concat([instrument_df, new_instr], ignore_index=True)

    fires_pm.to_parquet(cfg.FIRES_PM_PARQUET, index=False)
    instrument_df.to_parquet(cfg.INSTRUMENT_PARQUET, index=False)
    meta['fire_final_through'] = int(new_final)
    have = fires_pm.eval('year * 100 + month')
    meta['fire_prelim_through'] = int(have.max()) if len(have) else int(new_final)
    print(f'  fires artifact refreshed: final through {new_final}, '
          f'preliminary through {meta["fire_prelim_through"]}')
    return meta


def _build_panel(conflict_pm, fires_pm, instrument_df, district_wind, end_ym):
    all_districts = np.sort(conflict_pm['district'].unique())
    all_ym = pd.DataFrame(list(cfg.ym_iter(cfg.FETCH_START, end_ym)),
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

    panel = panel.sort_values(['district', 'year', 'month']).reset_index(drop=True)
    panel['log_frp_l1'] = panel.groupby('district')['log_frp'].shift(1)
    panel['log_upwind_frp_l1'] = panel.groupby('district')['log_upwind_frp'].shift(1)
    panel = panel.dropna(subset=['log_frp_l1', 'log_upwind_frp_l1']).reset_index(drop=True)
    panel['year_month'] = panel['year'] * 100 + panel['month']
    panel = panel[panel['year_month'] >= cfg.PANEL_START].reset_index(drop=True)
    return panel


def build_results(meta, districts):
    conflict_pm = pd.read_parquet(cfg.CONFLICT_PM_PARQUET)
    fires_pm = pd.read_parquet(cfg.FIRES_PM_PARQUET)
    instrument_df = pd.read_parquet(cfg.INSTRUMENT_PARQUET)
    district_wind = pd.read_parquet(cfg.WIND_PM_PARQUET)

    end_ym = min(meta['wind_through'], meta['fire_prelim_through'],
                 meta['acled_through'])
    print(f'Panel end: {end_ym} (wind {meta["wind_through"]}, '
          f'fire {meta["fire_prelim_through"]}, acled {meta["acled_through"]})')

    pnl = _build_panel(conflict_pm, fires_pm, instrument_df, district_wind, end_ym)
    prov = dict(zip(districts['district'], districts['province']))
    pnl['province'] = pnl['district'].map(prov)
    print(f'Panel: {len(pnl):,} obs, {pnl.district.nunique()} municipalities, '
          f'{pnl.year_month.nunique()} months')

    print('Estimating...')
    fs = estimate.first_stage(pnl)
    print(f'  first-stage F = {fs["f_stat"]:.0f}')
    full_iv = estimate.full_sample_iv(pnl)
    thresholds = estimate.conflict_active_table(pnl)
    twoway, fourway = estimate.event_type_tables(pnl)

    prelim_months = [int(ym) for ym in sorted(pnl['year_month'].unique())
                     if ym > meta['fire_final_through']]

    robustness = {}
    if cfg.ROBUSTNESS_JSON.exists():
        robustness = json.loads(cfg.ROBUSTNESS_JSON.read_text())

    results = {
        'meta': {
            'generated_at': datetime.now(timezone.utc).isoformat(),
            'panel_start': int(pnl['year_month'].min()),
            'panel_end': int(end_ym),
            'n_obs': int(len(pnl)),
            'n_districts': int(pnl['district'].nunique()),
            'n_months': int(pnl['year_month'].nunique()),
            'zero_share_events': float((pnl['events'] == 0).mean()),
            'vintages': {
                'acled_through': meta['acled_through'],
                'fire_final_through': meta['fire_final_through'],
                'fire_prelim_through': meta['fire_prelim_through'],
                'wind_through': meta['wind_through'],
            },
            'preliminary_months': prelim_months,
            'robustness': robustness,
        },
        'first_stage': fs,
        'full_iv': full_iv,
        'thresholds': thresholds,
        'event_types': {'twoway': twoway, 'fourway': fourway},
        'national_series': estimate.national_series(pnl),
        'district_latest': estimate.district_latest(pnl),
    }
    cfg.SITE_DATA_DIR.mkdir(parents=True, exist_ok=True)
    cfg.RESULTS_JSON.write_text(json.dumps(results))
    print(f'Wrote {cfg.RESULTS_JSON}')


def main():
    meta = json.loads(cfg.META_JSON.read_text())
    districts = load_districts()

    print('Updating conflict data (ACLED)...')
    try:
        meta = update_conflict(meta, districts)
    except Exception as exc:
        print(f'  ACLED update failed, keeping stored vintage: {exc}')

    print('Updating wind data (ERA5)...')
    try:
        meta = update_wind(meta, districts)
    except Exception as exc:
        print(f'  Wind update failed, keeping stored vintage: {exc}')

    print('Updating fire data (FIRMS)...')
    try:
        meta = update_fires(meta, districts)
    except Exception as exc:
        print(f'  Fire update failed, keeping stored vintage: {exc}')

    meta['last_run'] = datetime.now(timezone.utc).isoformat()
    cfg.META_JSON.write_text(json.dumps(meta, indent=2))

    build_results(meta, districts)


if __name__ == '__main__':
    main()
