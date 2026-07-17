"""Fetch ERA5 monthly-mean 10m winds for missing months.

Two backends, tried in order:
  earthmover - Earthmover's Arraylake marketplace. Defaults to the free
               public "earthmover-public/era5" repo (ARRAYLAKE_REPO),
               refreshed quarterly, no credentials required - just
               `pip install arraylake zarr`. Point ARRAYLAKE_REPO at
               "{your_org}/era5" and set ARRAYLAKE_TOKEN to use the paid
               "ERA5 (Daily Updates)" product instead (SLA: within 4 hours
               of ECMWF publication). The two editions nest hourly u10/v10
               under different group paths, so _fetch_month_earthmover
               probes for whichever exists rather than hardcoding one.
  cds        - Copernicus Climate Data Store API (free). ERA5T monthly means
               for month M appear around day 5-6 of month M+1. Used only when
               Earthmover isn't installed/configured or a given month's read
               fails there.

Both return district-agnostic gridded monthly means over the Indonesia box;
aggregation to districts happens in panel.py.
"""
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
import xarray as xr

from . import config


def _earthmover_available():
    try:
        import arraylake  # noqa: F401
        return True
    except ImportError:
        return False


def _wind_df_from_ds(ds, year, month):
    """Normalise an ERA5 monthly-mean dataset to a flat grid DataFrame."""
    for tdim in ('valid_time', 'time', 'date'):
        if tdim in ds.coords or tdim in ds.dims:
            ds = ds.rename({tdim: 'time'}) if tdim != 'time' else ds
            break
    u_name = next(v for v in ds.data_vars if v.lower() in
                  ('u10', '10m_u_component_of_wind', 'u_component_of_wind_10m'))
    v_name = next(v for v in ds.data_vars if v.lower() in
                  ('v10', '10m_v_component_of_wind', 'v_component_of_wind_10m'))
    df = (ds[[u_name, v_name]].to_dataframe().reset_index()
          .rename(columns={u_name: 'u10', v_name: 'v10'}))
    df = df.dropna(subset=['u10', 'v10'])
    df['year'], df['month'] = year, month
    return df[['latitude', 'longitude', 'year', 'month', 'u10', 'v10']]


def _fetch_month_cds(year, month):
    import cdsapi
    kwargs = {'url': config.CDSAPI_URL}
    if config.CDSAPI_KEY:
        kwargs['key'] = config.CDSAPI_KEY
    client = cdsapi.Client(**kwargs)
    n, s = config.BBOX[3], config.BBOX[1]
    w, e = config.BBOX[0], config.BBOX[2]
    with tempfile.TemporaryDirectory() as td:
        target = Path(td) / 'wind.nc'
        client.retrieve(config.CDS_DATASET, {
            'product_type': 'monthly_averaged_reanalysis',
            'variable': ['10m_u_component_of_wind', '10m_v_component_of_wind'],
            'year': [str(year)],
            'month': [f'{month:02d}'],
            'time': '00:00',
            'area': [n, w, s, e],
            'format': 'netcdf',
            'grid': '0.25/0.25',
        }, str(target))
        with xr.open_dataset(target) as ds:
            return _wind_df_from_ds(ds.load(), year, month)


def _fetch_month_earthmover(year, month):
    from arraylake import Client
    import zarr

    client = Client(token=config.ARRAYLAKE_TOKEN) if config.ARRAYLAKE_TOKEN else Client()
    repo = client.get_repo(config.ARRAYLAKE_REPO)
    session = repo.readonly_session(branch='main')

    # The free public repo and the paid "Daily Updates" repo nest hourly
    # single-level variables under different group paths - introspect
    # rather than hardcode one, so this works against either.
    root = zarr.open_group(session.store, zarr_format=3, mode='r')
    group_keys = set(root.group_keys())
    if 'single' in group_keys:
        group = 'single/temporal'   # paid tier
    elif 'temporal' in group_keys:
        group = 'temporal'          # free tier
    else:
        raise RuntimeError(
            f'Earthmover repo {config.ARRAYLAKE_REPO!r} has unexpected '
            f'top-level groups {sorted(group_keys)}')

    ds = xr.open_zarr(session.store, group=group, chunks=None)
    n, s = config.BBOX[3], config.BBOX[1]
    w, e = config.BBOX[0], config.BBOX[2]
    t0 = f'{year}-{month:02d}-01'
    t1 = pd.Timestamp(t0) + pd.offsets.MonthEnd(0) + pd.Timedelta(hours=23)
    sub = ds[['u10', 'v10']].sel(valid_time=slice(t0, t1))
    if sub.sizes.get('valid_time', 0) == 0:
        raise RuntimeError(
            f'Earthmover ERA5 ({config.ARRAYLAKE_REPO}) has no data for {year}-{month:02d}')

    # The paid daily-updated edition carries a per-hour QC status array
    # (0 = valid_data), needed because it streams in provisional ERA5T rows
    # that shouldn't silently zero-fill a monthly mean. The free edition is
    # a static, already-final reprocessed batch with no such array - skip
    # the check if it's absent rather than treat that as an error.
    try:
        status = xr.open_zarr(session.store, group=f'{group}/status', chunks=None)
        has_qc = 'u10' in status
    except Exception:
        has_qc = False  # no QC group in this edition - trust the data as-is
    if has_qc:
        qc = status['u10'].sel(valid_time=sub['valid_time'])
        sub = sub.where(qc == 0)
        if int((qc == 0).sum()) == 0:
            raise RuntimeError(f'Earthmover ERA5 has no valid_data QC rows for {year}-{month:02d}')

    lon0, lon1 = w % 360, e % 360
    lat = sub['latitude']
    lat_slice = slice(n, s) if lat.values[0] > lat.values[-1] else slice(s, n)
    sub = sub.sel(latitude=lat_slice, longitude=slice(lon0, lon1))
    monthly = sub.mean('valid_time', skipna=True, keep_attrs=True).expand_dims(
        time=[pd.Timestamp(t0)])
    df = _wind_df_from_ds(monthly.load(), year, month)
    df['longitude'] = ((df['longitude'] + 180) % 360) - 180  # 0-360 -> -180..180
    return df


def fetch_wind_months(months):
    """months: list of (year, month). Returns concatenated grid frame or None."""
    if not months:
        return pd.DataFrame()
    backends = []
    if _earthmover_available():
        backends.append(('earthmover', _fetch_month_earthmover))
    backends.append(('cds', _fetch_month_cds))

    frames = []
    for (y, m) in months:
        got = False
        for name, fn in backends:
            try:
                frames.append(fn(y, m))
                print(f'  wind: fetched {y}-{m:02d} via {name}')
                got = True
                break
            except Exception as exc:  # try next backend; month may not exist yet
                print(f'  wind: {name} failed for {y}-{m:02d}: {exc}')
        if not got:
            print(f'  wind: {y}-{m:02d} not yet available from any backend')
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def grid_to_district_wind(wind_grid, districts_gdf):
    """Assign ERA5 grid points to districts and average u/v per district-month.

    Ported from notebook cell 5: point-in-polygon join, with a
    nearest-grid-point fallback for districts smaller than the 0.25 degree
    grid, then the district-month mean of u10/v10 -> speed and direction.
    """
    import geopandas as gpd
    from scipy.spatial import cKDTree

    grid_points = (wind_grid[['latitude', 'longitude']]
                   .drop_duplicates().reset_index(drop=True))
    gg = gpd.GeoDataFrame(
        grid_points,
        geometry=gpd.points_from_xy(grid_points['longitude'], grid_points['latitude']),
        crs='EPSG:4326')
    joined = gpd.sjoin(gg, districts_gdf[['district', 'geometry']],
                       how='left', predicate='within')
    joined = joined[~joined.index.duplicated(keep='first')]
    grid_points['district'] = joined['district'].values

    have = set(grid_points['district'].dropna())
    missing = districts_gdf[~districts_gdf['district'].isin(have)]
    if len(missing):
        tree = cKDTree(grid_points[['latitude', 'longitude']].values)
        _, nn = tree.query(missing[['dist_lat', 'dist_lon']].values)
        fb = grid_points.iloc[nn][['latitude', 'longitude']].reset_index(drop=True)
        fb['district'] = missing['district'].values
        grid_points = pd.concat(
            [grid_points.dropna(subset=['district']), fb], ignore_index=True)
    else:
        grid_points = grid_points.dropna(subset=['district'])

    dw = wind_grid.merge(grid_points[['latitude', 'longitude', 'district']],
                         on=['latitude', 'longitude'], how='inner')
    dw = (dw.groupby(['district', 'year', 'month'])
          .agg(u10_mean=('u10', 'mean'), v10_mean=('v10', 'mean'))
          .reset_index())
    dw['wind_speed'] = np.sqrt(dw['u10_mean'] ** 2 + dw['v10_mean'] ** 2)
    dw['wind_dir_from'] = (np.degrees(
        np.arctan2(dw['u10_mean'], dw['v10_mean'])) + 180) % 360
    return dw
