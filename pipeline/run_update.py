"""Weekly update: fetch new data, extend the artifacts, re-estimate the wind
IV model, and write the JSON the dashboard reads.

Run:  python -m pipeline.run_update       (from the repo root)

Behaviour is graceful when a credential is missing: that source simply
isn't refreshed and the panel keeps its stored vintage, so the same entry
point works locally without secrets and in CI with them.
"""
import json
from datetime import date, datetime, timezone

import pandas as pd

from . import config, estimate, panel as panel_mod, spatial
from . import instrument as instr
from .fetch_acled import fetch_acled
from .fetch_fires import fetch_fires_since
from .fetch_wind import fetch_wind_months, grid_to_district_wind


def last_complete_month(today=None):
    today = today or date.today()
    y, m = (today.year, today.month - 1) if today.month > 1 else (today.year - 1, 12)
    return y * 100 + m


def update_conflict(meta, districts):
    acled = fetch_acled(start_date=f'{config.START_YEAR}-01-01')
    if acled is None:
        return meta
    conflict_pm = spatial.aggregate_conflict(acled, districts)
    # ACLED coverage: everything before the release week is complete; be
    # conservative and call the previous calendar month the last complete one.
    through = min(last_complete_month(),
                  int(conflict_pm.eval('year * 100 + month').max()))
    conflict_pm = conflict_pm[conflict_pm.eval('year * 100 + month') <= through]
    conflict_pm.to_parquet(config.CONFLICT_PM_PARQUET, index=False)
    meta['acled_through'] = through
    print(f'  conflict artifact replaced, through {through}')
    return meta


def update_wind(meta, districts):
    start = meta['wind_through']
    want = [(y, m) for (y, m) in config.ym_iter(_next(start), last_complete_month())]
    if not want:
        print('  wind: up to date')
        return meta
    grid = fetch_wind_months(want)
    if grid is None or grid.empty:
        return meta
    new_dw = grid_to_district_wind(grid, districts)
    dw = pd.read_parquet(config.WIND_PM_PARQUET)
    got = set(new_dw.eval('year * 100 + month').unique())
    dw = pd.concat([dw[~dw.eval('year * 100 + month').isin(got)], new_dw],
                   ignore_index=True)
    dw.to_parquet(config.WIND_PM_PARQUET, index=False)
    meta['wind_through'] = int(dw.eval('year * 100 + month').max())
    print(f'  wind artifact extended, through {meta["wind_through"]}')
    return meta


def update_fires(meta, districts):
    refetch_from = _next(meta['fire_final_through'])
    detections, new_final = fetch_fires_since(refetch_from)
    if detections is None:
        return meta
    fires_pm = pd.read_parquet(config.FIRES_PM_PARQUET)
    instrument_df = pd.read_parquet(config.INSTRUMENT_PARQUET)
    # Drop every stored month being refreshed (all months > old final cutoff)
    fires_pm = fires_pm[fires_pm.eval('year * 100 + month') < refetch_from]
    instrument_df = instrument_df[
        instrument_df.eval('year * 100 + month') < refetch_from]

    if len(detections):
        detections = spatial.assign_points_to_districts(
            detections.reset_index(drop=True), districts).dropna(subset=['district'])
        new_pm = spatial.aggregate_fires(detections, districts)
        fires_pm = pd.concat([fires_pm, new_pm], ignore_index=True)

        dw = pd.read_parquet(config.WIND_PM_PARQUET)
        months_with_fire = set(detections.eval('year * 100 + month').unique())
        dw_new = dw[dw.eval('year * 100 + month').isin(months_with_fire)]
        if len(dw_new):
            new_instr = instr.build_instrument(detections, dw_new, districts)
            instrument_df = pd.concat([instrument_df, new_instr], ignore_index=True)

    fires_pm.to_parquet(config.FIRES_PM_PARQUET, index=False)
    instrument_df.to_parquet(config.INSTRUMENT_PARQUET, index=False)
    meta['fire_final_through'] = int(new_final)
    have = fires_pm.eval('year * 100 + month')
    meta['fire_prelim_through'] = int(have.max()) if len(have) else int(new_final)
    print(f'  fires artifact refreshed: final through {new_final}, '
          f'preliminary through {meta["fire_prelim_through"]}')
    return meta


def _next(ym_val):
    y, m = divmod(ym_val, 100)
    return (y + 1) * 100 + 1 if m == 12 else ym_val + 1


def build_results(meta, districts):
    conflict_pm = pd.read_parquet(config.CONFLICT_PM_PARQUET)
    fires_pm = pd.read_parquet(config.FIRES_PM_PARQUET)
    instrument_df = pd.read_parquet(config.INSTRUMENT_PARQUET)
    district_wind = pd.read_parquet(config.WIND_PM_PARQUET)

    end_ym = min(meta['wind_through'], meta['fire_prelim_through'],
                 meta['acled_through'])
    print(f'Panel end: {end_ym} (wind {meta["wind_through"]}, '
          f'fire {meta["fire_prelim_through"]}, acled {meta["acled_through"]})')

    pnl = panel_mod.build_panel(conflict_pm, fires_pm, instrument_df,
                                district_wind, end_ym)
    pnl = panel_mod.attach_province(pnl, districts)
    print(f'Panel: {len(pnl):,} obs, {pnl.district.nunique()} districts, '
          f'{pnl.year_month.nunique()} months')

    print('Estimating...')
    fs = estimate.first_stage(pnl)
    print(f'  first-stage F = {fs["f_stat"]:.0f}')
    full_iv = estimate.full_sample_iv(pnl)
    thresholds = estimate.conflict_active_table(pnl)
    twoway, fourway = estimate.event_type_tables(pnl)
    expanding = estimate.expanding_window_series(pnl)

    prelim_months = [int(ym) for ym in sorted(pnl['year_month'].unique())
                     if ym > meta['fire_final_through']]

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
        },
        'first_stage': fs,
        'full_iv': full_iv,
        'thresholds': thresholds,
        'event_types': {'twoway': twoway, 'fourway': fourway},
        'expanding': expanding,
        'national_series': estimate.national_series(pnl),
        'district_latest': estimate.district_latest(pnl),
    }
    config.SITE_DATA_DIR.mkdir(parents=True, exist_ok=True)
    config.RESULTS_JSON.write_text(json.dumps(results))
    print(f'Wrote {config.RESULTS_JSON}')


def main():
    meta = json.loads(config.META_JSON.read_text())
    districts = spatial.load_districts()

    print('Updating conflict data (ACLED)...')
    meta = update_conflict(meta, districts)
    print('Updating wind data (ERA5)...')
    meta = update_wind(meta, districts)
    print('Updating fire data (FIRMS)...')
    meta = update_fires(meta, districts)

    meta['last_run'] = datetime.now(timezone.utc).isoformat()
    config.META_JSON.write_text(json.dumps(meta, indent=2))

    build_results(meta, districts)


if __name__ == '__main__':
    main()
