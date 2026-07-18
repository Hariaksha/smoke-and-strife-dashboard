"""One-time bootstrap: derive Nigeria's committed pipeline artifacts from
the already-verified nigeria_panel.parquet (built and validated across
build_panel.py / event_types.py / placebo_test.py / conley_se.py), rather
than refetching everything from scratch.

Key point: log_frp = np.log1p(total_frp) is EXACTLY invertible
(total_frp = np.expm1(log_frp)), so fires_pm.parquet and instrument.parquet
can be reconstructed directly from the panel's already-verified log_frp /
log_upwind_frp columns for Jan 2015 onward - skipping both the ~20-25 min
FIRMS historical refetch and, much more importantly, the 3+ hour KD-tree
instrument construction that was the actual bottleneck in the original
build.

Two things do need a fresh, small fetch, though:
  - wind_pm.parquet: wind_speed/wind_dir_from were never persisted anywhere
    (~6 min, year-batched CDS request, same as build_panel.py).
  - December 2014 specifically: nigeria_panel.parquet was already trimmed
    to start at Jan 2015 (Dec 2014 only existed transiently, to compute
    Jan 2015's lag, then got dropped before saving) - so it can't be
    recovered via the expm1 trick at all. Without it, a naive
    reconstruction would silently treat Dec 2014 as zero fire activity for
    every district, corrupting Jan 2015's lag control (caught by
    comparing this script's first-stage F against event_types.py's
    already-verified 6276.5 - a naive first pass came out to 6192.1,
    about 1.4% off, before this fix). Fixed by fetching that one buffer
    month's FIRMS data fresh (~1 month, a handful of 5-day-windowed
    requests) and building its instrument row from real December 2014
    fire detections and the freshly-fetched December 2014 wind.

Run:  python -m nigeria.pipeline_bootstrap        (from the repo root)
"""
import json
from datetime import date, datetime, timezone

import numpy as np
import pandas as pd

from . import config as cfg
from . import fires as fires_mod
from .wind import fetch_wind_months_batched
from pipeline import spatial, instrument as instr
from pipeline.fetch_wind import grid_to_district_wind


def main():
    cfg.DATA_DIR.mkdir(parents=True, exist_ok=True)
    cfg.SITE_DATA_DIR.mkdir(parents=True, exist_ok=True)

    print('=' * 70)
    print('NIGERIA BOOTSTRAP: reconstructing artifacts from nigeria_panel.parquet')
    print('=' * 70)

    panel = pd.read_parquet(cfg.PANEL_PARQUET)
    end_ym = int(panel['year_month'].max())
    print(f'\nLoaded panel: {len(panel):,} rows through {end_ym}')

    # ── Districts ────────────────────────────────────────────────────────
    print('\nDistricts: building from GADM shapefile...')
    districts = spatial.build_districts_from_gadm(cfg.SHP)
    districts.to_parquet(cfg.DISTRICTS_PARQUET)
    print(f'  {len(districts)} LGAs -> {cfg.DISTRICTS_PARQUET.name}')

    web = districts.copy()
    web['geometry'] = web.geometry.simplify(0.01, preserve_topology=True)
    web[['district', 'province', 'geometry']].to_file(
        cfg.DISTRICTS_GEOJSON, driver='GeoJSON')
    print(f'  web map geojson -> {cfg.DISTRICTS_GEOJSON.name}')

    # ── Wind: fetched first (year-batched), so Dec-2014 wind is on hand
    # for the buffer-month instrument fix below ───────────────────────────
    print('\nWind: fetching via CDS (year-batched)...')
    months = list(cfg.ym_iter(cfg.FETCH_START, end_ym))
    wind_grid = fetch_wind_months_batched(months).dropna(subset=['u10', 'v10'])
    district_wind = grid_to_district_wind(wind_grid, districts)
    district_wind.to_parquet(cfg.WIND_PM_PARQUET, index=False)
    wind_through = int(district_wind.eval('year * 100 + month').max())
    print(f'  {len(district_wind):,} district-month wind rows, through {wind_through}')

    # ── Conflict: direct slice, already has the event-type columns ────────
    print('\nConflict: slicing from panel (already crosswalked + tagged)...')
    conflict_cols = (['district', 'year', 'month', 'events', 'pv_events', 'fatalities',
                      'riots_protests', 'battles_violence']
                     + list(cfg.FOURWAY_TYPES))
    conflict_pm = panel[conflict_cols].copy()
    # Panel is zero-filled/cross-joined; only keep months with >=1 event of
    # any kind, matching how aggregate_conflict's groupby naturally excludes
    # all-zero district-months (the panel step re-adds zeros for the full grid).
    conflict_pm = conflict_pm[conflict_pm['events'] > 0].reset_index(drop=True)
    conflict_pm.to_parquet(cfg.CONFLICT_PM_PARQUET, index=False)
    acled_through = end_ym  # ACLED was fetched through this window originally
    print(f'  {len(conflict_pm):,} district-month conflict rows, through {acled_through}')

    # ── Fires: exact expm1 inversion of log_frp for Jan-2015 onward ───────
    print('\nFires: reconstructing via expm1(log_frp) (exact inverse of log1p)...')
    for x in [0.0, 1.0, 100.0, 12345.678]:
        assert abs(np.expm1(np.log1p(x)) - x) < 1e-6, f'expm1/log1p round-trip failed for {x}'
    print('  round-trip check (expm1(log1p(x)) == x) passed for spot-check values')

    fires_pm = panel[panel['log_frp'] > 0][['district', 'year', 'month', 'log_frp']].copy()
    fires_pm['total_frp'] = np.expm1(fires_pm['log_frp'])
    fires_pm = fires_pm[['district', 'year', 'month', 'total_frp', 'log_frp']]

    # ── Buffer month (Dec 2014): fetch fresh - not recoverable from the
    # already-trimmed panel, and required for Jan 2015's lag to be correct ─
    print(f'\nBuffer month {cfg.FETCH_START}: fetching FIRMS fresh (not in the saved panel)...')
    buf_y, buf_m = divmod(cfg.FETCH_START, 100)
    buf_start = date(buf_y, buf_m, 1)
    buf_end = date(buf_y, buf_m, 28)
    buf_fires = fires_mod._fetch_range('VIIRS_SNPP_SP', buf_start, buf_end)
    buf_fires = buf_fires[buf_fires['confidence'].astype(str).isin(['h', 'n'])].copy()
    if 'type' in buf_fires.columns:
        buf_fires = buf_fires[buf_fires['type'].fillna(0).astype(int).eq(0)]
    buf_fires['acq_date'] = pd.to_datetime(buf_fires['acq_date'])
    buf_fires['year'] = buf_fires['acq_date'].dt.year
    buf_fires['month'] = buf_fires['acq_date'].dt.month
    print(f'  {len(buf_fires):,} detections for {cfg.FETCH_START}')

    buf_assigned = spatial.assign_points_to_districts(
        buf_fires.reset_index(drop=True), districts).dropna(subset=['district'])
    buf_fires_pm = spatial.aggregate_fires(buf_assigned, districts)
    fires_pm = pd.concat(
        [fires_pm, buf_fires_pm[['district', 'year', 'month', 'total_frp', 'log_frp']]],
        ignore_index=True)
    fires_pm.to_parquet(cfg.FIRES_PM_PARQUET, index=False)
    fire_final_through = end_ym  # built entirely from VIIRS_SNPP_SP - no NRT tail needed
    print(f'  {len(fires_pm):,} district-month fire rows, final through {fire_final_through}')

    # ── Instrument: exact expm1 inversion for Jan-2015 onward, plus a real
    # Dec-2014 row built from the fresh detections + Dec-2014 wind ────────
    print('\nInstrument: reconstructing via expm1(log_upwind_frp)...')
    instrument_df = panel[['district', 'year', 'month', 'log_upwind_frp']].copy()
    instrument_df['upwind_frp'] = np.expm1(instrument_df['log_upwind_frp'])
    instrument_df = instrument_df[['district', 'year', 'month', 'upwind_frp', 'log_upwind_frp']]

    buf_dw = district_wind[district_wind.eval('year * 100 + month') == cfg.FETCH_START]
    buf_instr = instr.build_instrument(buf_fires, buf_dw, districts)
    instrument_df = pd.concat(
        [instrument_df, buf_instr[['district', 'year', 'month', 'upwind_frp', 'log_upwind_frp']]],
        ignore_index=True)
    instrument_df.to_parquet(cfg.INSTRUMENT_PARQUET, index=False)
    print(f'  {len(instrument_df):,} instrument rows, '
          f'{(instrument_df["upwind_frp"] == 0).mean() * 100:.1f}% zero-upwind')

    # ── Meta ───────────────────────────────────────────────────────────────
    meta = {
        'wind_through': wind_through,
        'fire_final_through': int(fire_final_through),
        'fire_prelim_through': int(fire_final_through),
        'acled_through': int(acled_through),
        'bootstrapped_at': datetime.now(timezone.utc).isoformat(),
    }
    cfg.META_JSON.write_text(json.dumps(meta, indent=2))
    print(f'\nBootstrap complete. meta = {meta}')


if __name__ == '__main__':
    main()
