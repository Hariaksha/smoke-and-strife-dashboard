"""Round-1 "crude gut check" for a new country candidate.

Answers one question cheaply, before investing in real GADM boundaries,
ACLED, and a full historical build: does the upwind fire-transport
instrument have any power at all in this country? Matches how Nigeria's
own scoping started (see ../nigeria/README.md's "Round 1" note: a coarse
1-degree analysis grid, no real district boundaries, fire+wind only, no
conflict side - first-stage F ~ 70 there was enough to justify Round 2).

No ACLED call at all (conflict isn't tested this round, so there's no
need to get a country's exact ACLED spelling right either). Only needs
FIRMS_MAP_KEY and CDSAPI_KEY.

Run (from repo root, after `source secrets.env`):
    python -m scoping.gutcheck --country Myanmar --bbox 92.2,9.5,101.2,28.5
    python -m scoping.gutcheck --country DRC --bbox 12.2,-13.5,31.5,5.5

Safe to run several of these at once in separate terminals - FIRMS/CDS
calls are per-country-bbox and nowhere near either service's rate limit
for a 3-month, 1-degree-grid pull.
"""
import argparse
import io
import json
import time
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pyfixest as pf
import requests

from pipeline import config as cfg
from pipeline import fetch_wind
from pipeline import instrument as instr

MAX_DAYS = 5          # FIRMS Area API's empirically-found per-key cap, not 10
GRID_DEG = 1.0
OUT_DIR = Path(__file__).resolve().parent / 'results'


def fetch_firms_range(bbox, source, start, end):
    area = ','.join(str(v) for v in bbox)
    frames = []
    cur = start
    while cur <= end:
        days = min(MAX_DAYS, (end - cur).days + 1)
        url = (f'{cfg.FIRMS_AREA_URL}/{cfg.FIRMS_MAP_KEY}/{source}/'
               f'{area}/{days}/{cur.isoformat()}')
        r = requests.get(url, timeout=300)
        r.raise_for_status()
        text = r.text.strip()
        if text and not text.lower().startswith('invalid'):
            chunk = pd.read_csv(io.StringIO(r.text))
            if len(chunk):
                frames.append(chunk)
        elif text.lower().startswith('invalid'):
            raise RuntimeError(f'FIRMS error: {text[:200]}')
        cur += timedelta(days=days)
        time.sleep(0.5)
    if not frames:
        return pd.DataFrame()
    df = pd.concat(frames, ignore_index=True)
    df = df[df['confidence'].astype(str).isin(['h', 'n'])].copy()
    if 'type' in df.columns:
        df = df[df['type'].fillna(0).astype(int).eq(0)]
    df['acq_date'] = pd.to_datetime(df['acq_date'])
    df['year'] = df['acq_date'].dt.year
    df['month'] = df['acq_date'].dt.month
    return df


def sp_last_available():
    url = f'{cfg.FIRMS_AVAIL_URL}/{cfg.FIRMS_MAP_KEY}/VIIRS_SNPP_SP'
    r = requests.get(url, timeout=60)
    r.raise_for_status()
    return pd.to_datetime(pd.read_csv(io.StringIO(r.text))['max_date'].iloc[0]).date()


def build_grid(bbox, deg=GRID_DEG):
    """Coarse lat/lon cells standing in for "districts" - centroids only,
    no polygons, since Round 1 skips real boundaries entirely."""
    west, south, east, north = bbox
    lons = np.arange(np.floor(west / deg) * deg, east + deg, deg)
    lats = np.arange(np.floor(south / deg) * deg, north + deg, deg)
    rows = [{'district': f'g{lat:.1f}_{lon:.1f}',
             'dist_lat': lat + deg / 2, 'dist_lon': lon + deg / 2}
            for lat in lats for lon in lons]
    return pd.DataFrame(rows)


def assign_to_grid(df, bbox, lat_col='latitude', lon_col='longitude', deg=GRID_DEG):
    # Bin to absolute degree lines (floor(coord/deg)*deg), matching
    # build_grid's cell edges exactly - binning relative to the bbox
    # corner instead would offset every fire/wind point into cell IDs
    # that never match build_grid's, silently zeroing the whole panel.
    glat = np.floor(df[lat_col] / deg) * deg
    glon = np.floor(df[lon_col] / deg) * deg
    out = df.copy()
    out['district'] = [f'g{la:.1f}_{lo:.1f}' for la, lo in zip(glat, glon)]
    return out


def grid_wind(wind_grid, bbox):
    g = assign_to_grid(wind_grid, bbox)
    dw = (g.groupby(['district', 'year', 'month'])
          .agg(u10_mean=('u10', 'mean'), v10_mean=('v10', 'mean'))
          .reset_index())
    dw['wind_dir_from'] = (np.degrees(
        np.arctan2(dw['u10_mean'], dw['v10_mean'])) + 180) % 360
    return dw


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--country', required=True)
    ap.add_argument('--bbox', required=True, help='west,south,east,north')
    ap.add_argument('--months', type=int, default=3)
    args = ap.parse_args()
    bbox = tuple(float(v) for v in args.bbox.split(','))
    slug = args.country.lower().replace(' ', '_')

    print(f'=== Round-1 gut check: {args.country} {bbox} ===')

    last_sp = sp_last_available()
    end = date(last_sp.year, last_sp.month, 1) - timedelta(days=1)
    months, y, m = [], end.year, end.month
    for _ in range(args.months):
        months.append((y, m))
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    months.reverse()
    start = date(months[0][0], months[0][1], 1)
    print(f'Fire/wind window: {months[0][0]}-{months[0][1]:02d} .. {months[-1][0]}-{months[-1][1]:02d}')

    print('Fetching FIRMS (VIIRS_SNPP_SP)...')
    fires = fetch_firms_range(bbox, 'VIIRS_SNPP_SP', start, end)
    print(f'  {len(fires):,} detections')
    if fires.empty:
        print('No fire detections in this bbox/window - stopping.')
        return

    print('Fetching ERA5 wind (CDS)...')
    cfg.BBOX = bbox  # fetch_wind._fetch_month_cds reads config.BBOX per-call
    wind_grid = fetch_wind.fetch_wind_months(months)
    if wind_grid.empty:
        print('No wind data - stopping.')
        return

    grid = build_grid(bbox)
    fires_g = assign_to_grid(fires, bbox)
    fires_pm = (fires_g.groupby(['district', 'year', 'month'])['frp'].sum()
                .reset_index().rename(columns={'frp': 'total_frp'}))
    fires_pm['log_frp'] = np.log1p(fires_pm['total_frp'])

    district_wind = grid_wind(wind_grid, bbox)
    print('Building upwind instrument...')
    instrument_df = instr.build_instrument(fires, district_wind, grid)

    all_ym = pd.DataFrame(months, columns=['year', 'month'])
    panel = pd.merge(grid[['district']], all_ym, how='cross')
    panel = panel.merge(fires_pm[['district', 'year', 'month', 'log_frp']],
                        on=['district', 'year', 'month'], how='left')
    panel = panel.merge(instrument_df[['district', 'year', 'month', 'log_upwind_frp']],
                        on=['district', 'year', 'month'], how='left')
    panel[['log_frp', 'log_upwind_frp']] = panel[['log_frp', 'log_upwind_frp']].fillna(0)
    panel['year_month'] = panel['year'] * 100 + panel['month']

    print(f'Panel: {len(panel):,} grid-cell-months, {grid.shape[0]} cells')
    res = pf.feols('log_frp ~ log_upwind_frp | district + year_month', data=panel)
    r2w, n = res._r2_within, res._N
    f_stat = (r2w / 1) / ((1 - r2w) / (n - 2))
    tidy = res.tidy().reset_index()
    row = tidy[tidy['Coefficient'] == 'log_upwind_frp'].iloc[0]
    coef, se, p = float(row['Estimate']), float(row['Std. Error']), float(row['Pr(>|t|)'])

    print()
    print(f'=== RESULT: {args.country} ===')
    print(f'  first-stage F = {f_stat:.1f}')
    print(f'  coef = {coef:.4f}, se = {se:.4f}, p = {p:.4g}')
    print(f'  {len(fires):,} fire detections, {grid.shape[0]} grid cells, {n} obs')
    print(f'  interpretation: F > ~10 means the instrument has real power here;')
    print(f'  F ~ 1-2 means little/no upwind-transport signal in this window.')

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUT_DIR / f'{slug}.json'
    out_path.write_text(json.dumps({
        'country': args.country, 'bbox': bbox, 'months': months,
        'n_fire_detections': int(len(fires)), 'n_grid_cells': int(grid.shape[0]),
        'n_obs': int(n), 'f_stat': f_stat, 'coef': coef, 'se': se, 'p': p,
    }, indent=2))
    print(f'\nWrote {out_path}')


if __name__ == '__main__':
    main()
