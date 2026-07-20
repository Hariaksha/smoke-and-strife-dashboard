"""One-time bootstrap: derive Brazil's committed pipeline artifacts from the
already-fetched, already-verified checkpoints in scoping/brazil_data/
(conflict_pm_full.parquet, fires_pm_full.parquet, instrument_full.parquet,
district_wind_full.parquet - built by scoping/brazil_round3.py, including
its 17.6-hour KD-tree instrument-construction run), rather than refetching
or recomputing anything.

Unlike nigeria/pipeline_bootstrap.py, no expm1(log1p(x)) reconstruction
trick is needed here - the scoping checkpoints already store the raw
total_frp / upwind_frp values directly, not just their logs. This script
is mostly a slice-and-window operation.

The one correction applied here that scoping/brazil_round3.py's own run
did NOT make: the scoping checkpoints span the full Dec-2014..Mar-2026
FIRMS-availability window, but ACLED's real Brazil coverage only runs
2018-01..2025-07 (confirmed via a raw ACLED pull - zero events before
2018-01-01, ACLED's usual ~12-month Research-tier lag after that). Every
artifact here is sliced to config.FETCH_START (201712) .. the min of
each source's actual max ym, so the committed panel never zero-fills a
month ACLED simply never covered.

Run:  python -m brazil.pipeline_bootstrap        (from the repo root)
"""
import json
from datetime import datetime, timezone

import pandas as pd

from . import config as cfg
from pipeline import spatial

SCOPING_DIR = cfg.REPO_ROOT / 'scoping' / 'brazil_data'


def _window(df, lo_ym, hi_ym):
    ym = df['year'] * 100 + df['month']
    return df[(ym >= lo_ym) & (ym <= hi_ym)].reset_index(drop=True)


def main():
    cfg.DATA_DIR.mkdir(parents=True, exist_ok=True)
    cfg.SITE_DATA_DIR.mkdir(parents=True, exist_ok=True)

    print('=' * 70)
    print('BRAZIL BOOTSTRAP: deriving artifacts from scoping/brazil_data/ checkpoints')
    print('=' * 70)

    if not cfg.BOOTSTRAP_SHP.exists():
        raise SystemExit(
            f'{cfg.BOOTSTRAP_SHP} not found - re-run scoping/brazil_round3.py '
            'first (or re-download the GADM shapefile) before bootstrapping.')

    # ── Districts: parsed once from the (uncommitted, local-only) raw
    # shapefile; the derived, simplified, small artifacts below are what
    # actually get committed and used at runtime from here on ────────────
    print('\nDistricts: building from GADM shapefile (one-time parse)...')
    districts = spatial.build_districts_from_gadm(cfg.BOOTSTRAP_SHP)
    districts.to_parquet(cfg.DISTRICTS_PARQUET)
    print(f'  {len(districts)} municipalities -> {cfg.DISTRICTS_PARQUET.name} '
          f'({cfg.DISTRICTS_PARQUET.stat().st_size / 1e6:.1f} MB)')

    web = districts.copy()
    web['geometry'] = web.geometry.simplify(0.01, preserve_topology=True)
    web[['district', 'province', 'geometry']].to_file(
        cfg.DISTRICTS_GEOJSON, driver='GeoJSON')
    print(f'  web map geojson -> {cfg.DISTRICTS_GEOJSON.name} '
          f'({cfg.DISTRICTS_GEOJSON.stat().st_size / 1e6:.1f} MB)')

    # ── Determine the true, ACLED-bounded window ───────────────────────────
    conflict_full = pd.read_parquet(SCOPING_DIR / 'conflict_pm_full.parquet')
    acled_through = int((conflict_full['year'] * 100 + conflict_full['month']).max())
    acled_from = int((conflict_full['year'] * 100 + conflict_full['month']).min())
    print(f'\nACLED real coverage in cached data: {acled_from}..{acled_through}')
    if acled_from > cfg.FETCH_START:
        print(f'  NOTE: cached ACLED data starts at {acled_from}, later than '
              f'configured FETCH_START {cfg.FETCH_START} - fires/instrument/wind '
              f'are still windowed from {cfg.FETCH_START} so the buffer month '
              f'needed for {acled_from}\'s lag control is real, not zero-filled.')
    hi_ym = acled_through

    # ── Conflict: direct slice, already has event-type columns, already
    # sparse (only district-months with >=1 event, from the groupby). Only
    # source windowed from acled_from, not FETCH_START - ACLED genuinely has
    # no rows before its real coverage start, so there is no buffer month to
    # preserve here (unlike fires/instrument/wind, see below) ─────────────
    conflict_pm = _window(conflict_full, acled_from, hi_ym)
    conflict_pm.to_parquet(cfg.CONFLICT_PM_PARQUET, index=False)
    print(f'\nConflict: {len(conflict_pm):,} district-month rows, '
          f'through {hi_ym} -> {cfg.CONFLICT_PM_PARQUET.name}')

    # ── Fires: windowed from FETCH_START (one month before acled_from) so
    # the panel has a real fire-detection buffer month to compute the first
    # ACLED-covered month's lag from, instead of losing that month to
    # dropna(subset=['log_frp_l1', ...]) ──────────────────────────────────
    fires_full = pd.read_parquet(SCOPING_DIR / 'fires_pm_full.parquet')
    fires_pm = _window(fires_full, cfg.FETCH_START, hi_ym)
    fires_pm.to_parquet(cfg.FIRES_PM_PARQUET, index=False)
    fire_through = int((fires_pm['year'] * 100 + fires_pm['month']).max())
    print(f'Fires: {len(fires_pm):,} district-month rows, through {fire_through} '
          f'-> {cfg.FIRES_PM_PARQUET.name}')

    # ── Instrument: same buffer-month reasoning as fires ────────────────────
    instrument_full = pd.read_parquet(SCOPING_DIR / 'instrument_full.parquet')
    instrument_df = _window(instrument_full, cfg.FETCH_START, hi_ym)
    instrument_df.to_parquet(cfg.INSTRUMENT_PARQUET, index=False)
    print(f'Instrument: {len(instrument_df):,} rows -> {cfg.INSTRUMENT_PARQUET.name}')

    # ── Wind: same buffer-month reasoning ────────────────────────────────────
    wind_full = pd.read_parquet(SCOPING_DIR / 'district_wind_full.parquet')
    district_wind = _window(wind_full, cfg.FETCH_START, hi_ym)
    district_wind.to_parquet(cfg.WIND_PM_PARQUET, index=False)
    wind_through = int((district_wind['year'] * 100 + district_wind['month']).max())
    print(f'Wind: {len(district_wind):,} district-month rows, through {wind_through} '
          f'-> {cfg.WIND_PM_PARQUET.name}')

    # ── Meta ───────────────────────────────────────────────────────────────
    # Built entirely from VIIRS_SNPP_SP (confirmed in scoping/brazil_round3.py's
    # run) - no NRT tail, so fire final == preliminary through here.
    meta = {
        'wind_through': wind_through,
        'fire_final_through': fire_through,
        'fire_prelim_through': fire_through,
        'acled_through': hi_ym,
        'bootstrapped_at': datetime.now(timezone.utc).isoformat(),
    }
    cfg.META_JSON.write_text(json.dumps(meta, indent=2))
    print(f'\nBootstrap complete. meta = {meta}')


if __name__ == '__main__':
    main()
