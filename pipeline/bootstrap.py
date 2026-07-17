"""One-time bootstrap: build the committed pipeline artifacts from the local
archive files used in the paper (GADM shapefile, ERA5 netCDF, VIIRS archive
CSV, ACLED xlsx). Weekly CI runs never touch these - they extend the
artifacts incrementally via the live APIs.

Run:  python -m pipeline.bootstrap        (from the repo root)
"""
import json
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import xarray as xr

from . import config, spatial, instrument as instr
from .fetch_wind import grid_to_district_wind


def month_before(ts):
    prev = ts.to_period('M') - 1
    return prev.year * 100 + prev.month


def main():
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    config.SITE_DATA_DIR.mkdir(parents=True, exist_ok=True)

    # ── Districts ─────────────────────────────────────────────────────────
    print('Districts: building from GADM shapefile...')
    districts = spatial.build_districts_from_gadm(config.BOOT_DISTRICT_SHP)
    districts.to_parquet(config.DISTRICTS_PARQUET)
    print(f'  {len(districts)} districts -> {config.DISTRICTS_PARQUET.name}')

    # Coarser copy for the web map
    web = districts.copy()
    web['geometry'] = web.geometry.simplify(0.01, preserve_topology=True)
    web[['district', 'province', 'geometry']].to_file(
        config.DISTRICTS_GEOJSON, driver='GeoJSON')
    print(f'  web map geojson -> {config.DISTRICTS_GEOJSON.name}')

    # ── Wind ──────────────────────────────────────────────────────────────
    print('Wind: reading archive netCDF...')
    ds = xr.open_dataset(config.BOOT_WIND_NC)
    if 'valid_time' in ds.coords:
        ds = ds.rename({'valid_time': 'time'})
    u = ds['u10'] if 'u10' in ds else ds[list(ds.data_vars)[0]]
    v = ds['v10'] if 'v10' in ds else ds[list(ds.data_vars)[1]]
    wind_grid = (u.to_dataframe(name='u10').reset_index()
                 .merge(v.to_dataframe(name='v10').reset_index(),
                        on=['time', 'latitude', 'longitude']))
    wind_grid['time'] = pd.to_datetime(wind_grid['time'])
    wind_grid['year'] = wind_grid['time'].dt.year
    wind_grid['month'] = wind_grid['time'].dt.month
    wind_grid = wind_grid[wind_grid['year'] >= config.START_YEAR]

    district_wind = grid_to_district_wind(wind_grid, districts)
    district_wind.to_parquet(config.WIND_PM_PARQUET, index=False)
    wind_through = int(district_wind.eval('year * 100 + month').max())
    print(f'  {len(district_wind):,} district-months, through {wind_through}')

    # ── Fires ─────────────────────────────────────────────────────────────
    print('Fires: reading VIIRS archive CSV (this takes a minute)...')
    fires = pd.read_csv(
        config.BOOT_FIRE_CSV,
        usecols=['acq_date', 'latitude', 'longitude', 'frp', 'confidence', 'type'],
        dtype={'confidence': str, 'type': 'Int64'})
    fires = fires[(fires['type'] == 0) &
                  fires['confidence'].isin(['h', 'n'])].copy()
    fires['acq_date'] = pd.to_datetime(fires['acq_date'])
    fires['year'] = fires['acq_date'].dt.year
    fires['month'] = fires['acq_date'].dt.month
    fires = fires[fires['year'] >= config.START_YEAR]

    # Archive is SP (final) data; the last month present may be partial, so
    # treat only months strictly before it as final and store only those.
    fire_final_through = month_before(fires['acq_date'].max())
    fires = fires[fires.eval('year * 100 + month') <= fire_final_through]
    print(f'  {len(fires):,} detections after filters, final through {fire_final_through}')

    fires = spatial.assign_points_to_districts(fires.reset_index(drop=True), districts)
    fires = fires.dropna(subset=['district'])
    fires_pm = spatial.aggregate_fires(fires, districts)
    fires_pm.to_parquet(config.FIRES_PM_PARQUET, index=False)
    print(f'  {len(fires_pm):,} district-month fire rows')

    # ── Instrument (needs wind + detections; slowest step) ────────────────
    print('Instrument: constructing upwind exposure for all district-months...')
    dw = district_wind[district_wind.eval('year * 100 + month') <= fire_final_through]
    instrument_df = instr.build_instrument(fires, dw, districts)
    instrument_df.to_parquet(config.INSTRUMENT_PARQUET, index=False)
    print(f'  {len(instrument_df):,} instrument rows, '
          f'{(instrument_df["upwind_frp"] == 0).mean() * 100:.1f}% zero-upwind')

    # ── Conflict ──────────────────────────────────────────────────────────
    print('Conflict: reading ACLED xlsx...')
    acled = pd.read_excel(
        config.BOOT_ACLED_XLSX,
        usecols=['event_date', 'admin1', 'admin2', 'event_type', 'fatalities',
                 'latitude', 'longitude'])
    acled_through = month_before(pd.to_datetime(acled['event_date']).max())
    conflict_pm = spatial.aggregate_conflict(acled, districts)
    conflict_pm = conflict_pm[conflict_pm.eval('year * 100 + month') <= acled_through]
    conflict_pm.to_parquet(config.CONFLICT_PM_PARQUET, index=False)
    print(f'  {len(conflict_pm):,} district-month conflict rows, through {acled_through}')

    # ── Meta ──────────────────────────────────────────────────────────────
    meta = {
        'wind_through': wind_through,
        'fire_final_through': int(fire_final_through),
        'fire_prelim_through': int(fire_final_through),
        'acled_through': int(acled_through),
        'bootstrapped_at': datetime.now(timezone.utc).isoformat(),
    }
    config.META_JSON.write_text(json.dumps(meta, indent=2))
    print(f'Bootstrap complete. meta = {meta}')


if __name__ == '__main__':
    main()
