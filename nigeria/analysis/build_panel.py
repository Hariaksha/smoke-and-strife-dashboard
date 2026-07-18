"""Build a real district-month wind-IV panel for Nigeria, reusing the
proven building blocks from the Indonesia dashboard pipeline (spatial
joins, instrument construction, estimation) but with:
  - real GADM level-2 LGA boundaries (775 units) instead of the earlier
    1-degree grid gut check
  - a from-scratch ACLED admin2 -> GADM LGA crosswalk (English-English,
    so no translation table needed unlike Indonesia's ACLED English /
    GADM Indonesian mismatch - direct match + fuzzy fallback)
  - the full 2015-01 through 2025-07 window, matching the paper's own
    Indonesia span (2025-07 is where ACLED's Research-tier ~12-month lag
    caps real data anyway, as of 2026-07-17). VIIRS fire data only exists
    from ~2012 on, so 2015 (not further back) also matches the constraint
    that motivated the paper's own start year.
  - wind fetched in one CDS request PER YEAR (11 requests) rather than
    per month (127 requests) - CDS's API accepts a list of months in a
    single call, cutting the wind step from ~50-60 min to an estimated
    ~10-15 min of queue waits.

Lives inside smoke-and-strife-dashboard (imports its pipeline package) but
is NOT part of the production weekly-updating dashboard/site - a research
script for the Nigeria scoping exercise, kept in its own nigeria/ subtree
so it stays clearly separate until/unless it's wired into site/ with a
country selector.

To run:
    source /Users/hariaksha/Documents/GitHub/smoke-and-strife-dashboard/secrets.env
    /Users/hariaksha/Documents/GitHub/climate-conflict/.venv/bin/python \\
        /Users/hariaksha/Documents/GitHub/smoke-and-strife-dashboard/nigeria/analysis/build_panel.py

Actual observed runtime for the full 2015-01–2025-07 window (2026-07-17
run): ~3.5 hours total, dominated by instrument construction taking over
3 hours alone (not the 10-20 min originally estimated) - Nigeria's fire
density (5.8M detections vs. Indonesia's ~2.1M over a comparable span)
makes each KD-tree radius query costlier per row, not just more numerous;
that scaling turned out to be markedly worse than linear. FIRMS (~20 min)
and the year-batched wind fetch (~6 min) matched their estimates closely.
"""
import difflib
import io
import sys
import tempfile
import time
from datetime import date, timedelta
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import pyfixest as pf
import requests
import xarray as xr

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # repo root (nigeria/ now lives inside it)
from pipeline import config as _cfg
from pipeline import spatial, instrument as instr, estimate
from pipeline.fetch_wind import grid_to_district_wind

HERE = Path(__file__).resolve().parents[1]
SHP = HERE / 'data/administrative/gadm41_NGA_shp/gadm41_NGA_2.shp'
BBOX = (2.5, 4.0, 14.8, 14.0)  # west, south, east, north
PANEL_START, PANEL_END = 201501, 202507  # need 201412 too, for the lag
FETCH_START = 201412
N_LAGS = 1

print('=' * 70)
print('NIGERIA WIND-IV PANEL: real GADM boundaries, real ACLED/FIRMS/ERA5')
print('=' * 70)

# ── 1. Districts ────────────────────────────────────────────────────────
print('\n[1] Districts from GADM...')
districts = spatial.build_districts_from_gadm(SHP)
print(f'  {len(districts)} LGAs loaded')

# ── 2. ACLED: full Nigeria pull, own crosswalk (no translation needed) ──
print('\n[2] ACLED conflict data...')


def fetch_acled_country(country, start_date):
    r = requests.post(_cfg.ACLED_TOKEN_URL, data={
        'username': _cfg.ACLED_EMAIL, 'password': _cfg.ACLED_PASSWORD,
        'grant_type': 'password', 'client_id': 'acled', 'scope': 'authenticated',
    }, timeout=60)
    r.raise_for_status()
    token = r.json()['access_token']
    headers = {'Authorization': f'Bearer {token}'}
    fields = 'event_date|admin1|admin2|event_type|fatalities|latitude|longitude'
    frames, page = [], 1
    while True:
        resp = requests.get(_cfg.ACLED_READ_URL, headers=headers, params={
            '_format': 'csv', 'country': country,
            'event_date': f'{start_date}|2099-12-31', 'event_date_where': 'BETWEEN',
            'fields': fields, 'limit': 5000, 'page': page,
        }, timeout=300)
        resp.raise_for_status()
        chunk = pd.read_csv(io.StringIO(resp.text))
        if chunk.empty:
            break
        frames.append(chunk)
        if len(chunk) < 5000:
            break
        page += 1
    return pd.concat(frames, ignore_index=True)


acled = fetch_acled_country('Nigeria', '2015-01-01')
print(f'  {len(acled):,} events fetched ({acled.event_date.min()} to {acled.event_date.max()})')


def build_crosswalk_direct_fuzzy(acled_names, gadm_names):
    """English ACLED admin2 -> English GADM LGA: direct match, then a
    fuzzy fallback (difflib) for spelling variants. No translation table
    needed, unlike Indonesia's ACLED-English/GADM-Indonesian mismatch."""
    gadm_list = list(gadm_names)
    mapping, unmatched, fuzzy = {}, [], []
    for name in acled_names:
        if name in gadm_names:
            mapping[name] = name
            continue
        close = difflib.get_close_matches(name, gadm_list, n=1, cutoff=0.82)
        if close:
            mapping[name] = close[0]
            fuzzy.append((name, close[0]))
        else:
            unmatched.append(name)
    return mapping, unmatched, fuzzy


acled['event_date'] = pd.to_datetime(acled['event_date'])
acled['year'] = acled['event_date'].dt.year
acled['month'] = acled['event_date'].dt.month
names = acled['admin2'].dropna().unique()
gadm_names = set(districts['district'])
mapping, unmatched, fuzzy = build_crosswalk_direct_fuzzy(names, gadm_names)
print(f'  crosswalk: {len(mapping)}/{len(names)} admin2 names matched '
      f'({len(fuzzy)} via fuzzy match, {len(unmatched)} unmatched)')
if fuzzy:
    print(f'  sample fuzzy matches: {fuzzy[:5]}')
if unmatched:
    print(f'  unmatched (dropped): {sorted(unmatched)[:10]}')

acled['district'] = acled['admin2'].map(mapping)
matched = acled.dropna(subset=['district']).copy()
print(f'  {len(matched):,}/{len(acled):,} events retained after crosswalk')

matched['is_pv'] = matched['event_type'].isin(_cfg.POLITICAL_VIOLENCE)
conflict_pm = (matched.groupby(['district', 'year', 'month'])
               .agg(events=('event_type', 'count'), pv_events=('is_pv', 'sum'),
                    fatalities=('fatalities', 'sum'))
               .reset_index())
conflict_pm[['events', 'pv_events', 'fatalities']] = (
    conflict_pm[['events', 'pv_events', 'fatalities']].astype(int))

# ── 3. Fires: FIRMS SP archive, 5-day windows (the real cap, not 10) ────
print('\n[3] FIRMS fire detections...')
AREA = ','.join(str(v) for v in BBOX)


def fetch_fires_range(source, start, end):
    frames, cur = [], start
    while cur <= end:
        days = min(5, (end - cur).days + 1)
        url = (f'{_cfg.FIRMS_AREA_URL}/{_cfg.FIRMS_MAP_KEY}/{source}/'
               f'{AREA}/{days}/{cur.isoformat()}')
        r = requests.get(url, timeout=300)
        r.raise_for_status()
        text = r.text.strip()
        if text and not text.lower().startswith('invalid'):
            chunk = pd.read_csv(io.StringIO(r.text))
            if len(chunk):
                frames.append(chunk)
        cur += timedelta(days=days)
        time.sleep(0.3)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


fetch_y, fetch_m = divmod(FETCH_START, 100)
end_y, end_m = divmod(PANEL_END, 100)
fires_raw = fetch_fires_range('VIIRS_SNPP_SP', date(fetch_y, fetch_m, 1),
                              date(end_y, end_m, 28))
fires_raw = fires_raw[fires_raw['confidence'].astype(str).isin(['h', 'n'])].copy()
if 'type' in fires_raw.columns:
    fires_raw = fires_raw[fires_raw['type'].fillna(0).astype(int).eq(0)]
fires_raw['acq_date'] = pd.to_datetime(fires_raw['acq_date'])
fires_raw['year'] = fires_raw['acq_date'].dt.year
fires_raw['month'] = fires_raw['acq_date'].dt.month
print(f'  {len(fires_raw):,} detections after filters')

fires = spatial.assign_points_to_districts(fires_raw, districts)
fires = fires.dropna(subset=['district']).reset_index(drop=True)
fires_pm = spatial.aggregate_fires(fires, districts)
print(f'  {len(fires_pm):,} district-month fire rows')

# ── 4. Wind: CDS, one request per YEAR (not per month) ─────────────────
# CDS's API accepts a list of months in a single request, so batching by
# year cuts this from ~127 queue-waits (~25-30s each, ~50-60 min total)
# down to ~11 (one per calendar year touched).
print('\n[4] ERA5 wind (CDS, batched by year)...')


def fetch_wind_year_batched(year, months_wanted):
    """One CDS request for every month in `months_wanted` within `year`."""
    import cdsapi
    kwargs = {'url': _cfg.CDSAPI_URL}
    if _cfg.CDSAPI_KEY:
        kwargs['key'] = _cfg.CDSAPI_KEY
    client = cdsapi.Client(**kwargs)
    n, s = BBOX[3], BBOX[1]
    w, e = BBOX[0], BBOX[2]
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


months = list(_cfg.ym_iter(FETCH_START, PANEL_END))
by_year = {}
for y, m in months:
    by_year.setdefault(y, []).append(m)

wind_frames = []
for year, months_wanted in sorted(by_year.items()):
    try:
        wind_frames.append(fetch_wind_year_batched(year, months_wanted))
        print(f'  wind: fetched {year} ({len(months_wanted)} months) via cds')
    except Exception as exc:
        print(f'  wind: {year} failed, falling back to per-month for that year: {exc}')
        from pipeline.fetch_wind import fetch_wind_months
        _orig_bbox = _cfg.BBOX
        _cfg.BBOX = BBOX
        try:
            wind_frames.append(fetch_wind_months([(year, m) for m in months_wanted]))
        finally:
            _cfg.BBOX = _orig_bbox
wind_grid = pd.concat(wind_frames, ignore_index=True)
wind_grid = wind_grid.dropna(subset=['u10', 'v10'])
district_wind = grid_to_district_wind(wind_grid, districts)
print(f'  {len(district_wind):,} district-month wind rows')

# ── 5. Upwind instrument (same function, same 300km/45deg params) ──────
print('\n[5] Upwind instrument...')
instrument_df = instr.build_instrument(fires_raw.dropna(subset=['latitude', 'longitude']),
                                       district_wind, districts)
print(f'  {len(instrument_df):,} instrument rows, '
      f'{(instrument_df.upwind_frp == 0).mean()*100:.1f}% zero-upwind')

# ── 6. Panel assembly ─────────────────────────────────────────────────
print('\n[6] Assembling panel...')
all_districts = np.sort(conflict_pm['district'].unique())
all_ym = pd.DataFrame(list(_cfg.ym_iter(FETCH_START, PANEL_END)), columns=['year', 'month'])
panel = pd.merge(pd.DataFrame({'district': all_districts}), all_ym, how='cross')
panel = panel.merge(conflict_pm, on=['district', 'year', 'month'], how='left')
for c in ['events', 'pv_events', 'fatalities']:
    panel[c] = panel[c].fillna(0).astype(int)
panel = panel.merge(fires_pm[['district', 'year', 'month', 'log_frp']],
                    on=['district', 'year', 'month'], how='left')
panel['log_frp'] = panel['log_frp'].fillna(0)
panel = panel.merge(instrument_df[['district', 'year', 'month', 'log_upwind_frp']],
                    on=['district', 'year', 'month'], how='left')
panel['log_upwind_frp'] = panel['log_upwind_frp'].fillna(0)
panel = panel.sort_values(['district', 'year', 'month']).reset_index(drop=True)
panel['log_frp_l1'] = panel.groupby('district')['log_frp'].shift(1)
panel['log_upwind_frp_l1'] = panel.groupby('district')['log_upwind_frp'].shift(1)
panel = panel.dropna(subset=['log_frp_l1', 'log_upwind_frp_l1']).reset_index(drop=True)
panel['year_month'] = panel['year'] * 100 + panel['month']
panel = panel[panel['year_month'] >= PANEL_START].reset_index(drop=True)
prov = dict(zip(districts['district'], districts['province']))
panel['province'] = panel['district'].map(prov)

print(f'  Panel: {len(panel):,} district-months, {panel.district.nunique()} districts, '
      f'{panel.year_month.nunique()} months')
print(f'  Zero-event share: {(panel["events"]==0).mean()*100:.1f}%')

panel.to_parquet(HERE / 'analysis' / 'nigeria_panel.parquet', index=False)
print(f'  Saved: {HERE / "analysis/nigeria_panel.parquet"}')

# ── 7. Estimation (reusing pipeline.estimate - panel-shape agnostic) ───
print('\n[7] Estimation...')
fs = estimate.first_stage(panel)
print(f'  First-stage F = {fs["f_stat"]:.1f} '
      f'(coef {fs["contemporaneous"]["coef"]:.3f}, p={fs["contemporaneous"]["p"]:.4f})')

full_iv = estimate.full_sample_iv(panel)
print('\n  Full-panel IV:')
for row in full_iv:
    print(f'    {row["label"]:6s} {row["outcome"]:10s} coef={row["coef"]:+.4f} '
          f'p={row["p"]:.3f} n={row["n"]:,}')

print('\n  Conflict-active heterogeneity:')
thresholds = estimate.conflict_active_table(panel)
for row in thresholds:
    print(f'    tau>={row["threshold"]:.0%}: {row["n_districts"]} districts, '
          f'F={row["first_stage_F"]:.0f}, '
          f'events coef={row["events"]["coef"]:+.4f} (p={row["events"]["p"]:.3f}), '
          f'pv coef={row["pv_events"]["coef"]:+.4f} (p={row["pv_events"]["p"]:.3f})')

print('\nDone.')
