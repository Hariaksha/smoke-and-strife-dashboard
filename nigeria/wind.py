"""Nigeria wind fetch: one CDS request per YEAR (not per month) - CDS's
API accepts a list of months in a single request, cutting a multi-year
fetch from ~25-30s/month to ~25-30s/YEAR. Extracted from build_panel.py
(where this was first built and verified: 12 months of 2015 fetched in a
single 26-second request) so pipeline_bootstrap.py can reuse it correctly,
instead of falling back to pipeline.fetch_wind.fetch_wind_months, which
loops month-by-month internally and would silently lose the speedup.
"""
import tempfile
from pathlib import Path

import pandas as pd
import xarray as xr

from . import config as cfg


def fetch_wind_year_batched(year, months_wanted):
    """One CDS request for every month in `months_wanted` within `year`."""
    import cdsapi
    from pipeline import config as _idn_cfg

    kwargs = {'url': _idn_cfg.CDSAPI_URL}
    if _idn_cfg.CDSAPI_KEY:
        kwargs['key'] = _idn_cfg.CDSAPI_KEY
    client = cdsapi.Client(**kwargs)
    w, s, e, n = cfg.BBOX
    with tempfile.TemporaryDirectory() as td:
        target = Path(td) / 'wind.nc'
        client.retrieve('reanalysis-era5-single-levels-monthly-means', {
            'product_type': 'monthly_averaged_reanalysis',
            'variable': ['10m_u_component_of_wind', '10m_v_component_of_wind'],
            'year': [str(year)],
            'month': [f'{m:02d}' for m in months_wanted],
            'time': '00:00',
            'area': [n, w, s, e],
            'format': 'netcdf',
            'grid': '0.25/0.25',
        }, str(target))
        with xr.open_dataset(target) as ds:
            ds = ds.load()
    for tdim in ('valid_time', 'time', 'date'):
        if tdim in ds.coords or tdim in ds.dims:
            if tdim != 'time':
                ds = ds.rename({tdim: 'time'})
            break
    df = ds[['u10', 'v10']].to_dataframe().reset_index()
    df['year'] = pd.to_datetime(df['time']).dt.year
    df['month'] = pd.to_datetime(df['time']).dt.month
    return df[['latitude', 'longitude', 'year', 'month', 'u10', 'v10']].dropna(
        subset=['u10', 'v10'])


def fetch_wind_months_batched(months):
    """months: list of (year, month) tuples, possibly spanning multiple
    years. Groups by year and issues one request per year."""
    by_year = {}
    for y, m in months:
        by_year.setdefault(y, []).append(m)

    frames = []
    for year, months_wanted in sorted(by_year.items()):
        try:
            frames.append(fetch_wind_year_batched(year, months_wanted))
            print(f'  wind: fetched {year} ({len(months_wanted)} months) via cds')
        except Exception as exc:
            print(f'  wind: {year} failed: {exc}')
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
