"""Round-2 scoping for Myanmar: real GADM township boundaries + a real
ACLED pull, over a ~24-month window (not the full history) - the same
"dress rehearsal" step Nigeria's own scoping did before committing to a
full multi-year build (see nigeria/README.md's "Round 2").

Round 1 (scoping/gutcheck.py) already confirmed the upwind-fire
instrument has real power in Myanmar (F=54.8, coarse 1-degree grid, no
real boundaries, no conflict). This round checks two more things at low
cost before investing in the ~3+ hour full historical build:
  1. Does the instrument still hold with real townships instead of a
     1-degree grid?
  2. Does ACLED's conflict data actually crosswalk cleanly onto GADM
     township names, and does the conflict-active heterogeneity shape
     look anything like Indonesia's/Nigeria's?

Downloads Myanmar's GADM 4.1 shapefile itself (~a few MB) on first run.

Run (from repo root):
    source /Users/hariaksha/Documents/GitHub/climate-conflict/.venv/bin/activate
    source secrets.env
    python -m scoping.myanmar_round2

Needs ACLED_EMAIL, ACLED_PASSWORD, FIRMS_MAP_KEY, CDSAPI_KEY (all four,
unlike Round 1 - this round tests the conflict crosswalk too). Takes
maybe 15-25 minutes: ACLED ~15s, FIRMS ~2-4 min, wind ~1 min/month (24
months), instrument construction is the unknown (Round 1's 231 grid
cells took seconds; ~330 real townships over 24 months should still be
well under Nigeria's 3+ hour full-decade figure, but this is the first
real measurement of it).
"""
import difflib
import io
import time
import zipfile
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import requests

from pipeline import config as cfg
from pipeline import spatial
from pipeline import instrument as instr
from pipeline import estimate
from pipeline.fetch_wind import fetch_wind_months, grid_to_district_wind
from scoping.gutcheck import fetch_firms_range, sp_last_available

HERE = Path(__file__).resolve().parent
DATA_DIR = HERE / 'myanmar_data'
SHP_DIR = DATA_DIR / 'gadm41_MMR_shp'
# level 2 = District (63, too coarse - ACLED's admin2 field for Myanmar is
# actually at the Township level, GADM level 3, 286 units). Confirmed by
# the first real run of this script: only 47/82 ACLED admin2 names matched
# against level-2 District names, and the unmatched ones (Falam, Gangaw,
# Hakha, Kale, Kanbalu, ...) are real townships, not district-name spelling
# variants - a level mismatch, not something fuzzy matching can fix.
SHP_PATH = SHP_DIR / 'gadm41_MMR_3.shp'
GADM_URL = 'https://geodata.ucdavis.edu/gadm/gadm4.1/shp/gadm41_MMR_shp.zip'

BBOX = (92.2, 9.5, 101.2, 28.5)  # west, south, east, north
COUNTRY = 'Myanmar'
WINDOW_MONTHS = 24


def build_myanmar_districts(shp_path):
    """Like pipeline.spatial.build_districts_from_gadm, but one level
    deeper: NAME_2 (District) is the clustering group and NAME_3
    (Township) is the actual spatial unit - Myanmar's GADM hierarchy has
    an extra level (State > District > Township) that Indonesia/Nigeria's
    doesn't, and ACLED's admin2 field reports at the Township level."""
    import geopandas as gpd
    gdf = gpd.read_file(shp_path)[['NAME_2', 'NAME_3', 'geometry']].rename(
        columns={'NAME_2': 'province', 'NAME_3': 'district'})
    dup = gdf['district'].duplicated(keep=False)
    gdf.loc[dup, 'district'] = (
        gdf.loc[dup, 'district'] + ' (' + gdf.loc[dup, 'province'] + ')')
    rep = gdf.geometry.representative_point()
    gdf['dist_lat'] = rep.y
    gdf['dist_lon'] = rep.x
    gdf['geometry'] = gdf.geometry.simplify(0.001, preserve_topology=True)
    return gdf


def ensure_shapefile():
    if SHP_PATH.exists():
        print(f'GADM shapefile already present at {SHP_PATH}')
        return
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    zip_path = DATA_DIR / 'gadm41_MMR_shp.zip'
    print(f'Downloading {GADM_URL} ...')
    r = requests.get(GADM_URL, timeout=300)
    r.raise_for_status()
    zip_path.write_bytes(r.content)
    print(f'  {len(r.content) / 1e6:.1f} MB downloaded, extracting...')
    SHP_DIR.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as z:
        z.extractall(SHP_DIR)
    zip_path.unlink()
    print(f'  extracted to {SHP_DIR}')


def fetch_acled_country(country, start_date):
    r = requests.post(cfg.ACLED_TOKEN_URL, data={
        'username': cfg.ACLED_EMAIL, 'password': cfg.ACLED_PASSWORD,
        'grant_type': 'password', 'client_id': 'acled', 'scope': 'authenticated',
    }, timeout=60)
    r.raise_for_status()
    token = r.json()['access_token']
    headers = {'Authorization': f'Bearer {token}'}
    fields = 'event_date|admin1|admin2|event_type|fatalities|latitude|longitude'
    frames, page = [], 1
    while True:
        resp = requests.get(cfg.ACLED_READ_URL, headers=headers, params={
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
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def build_crosswalk_direct_fuzzy(acled_names, gadm_names):
    """Direct match, then a fuzzy fallback for spelling variants - no
    translation table needed since ACLED and GADM are both English here
    (same approach as nigeria/acled.py, which needed the same thing)."""
    gadm_list = list(gadm_names)
    mapping, unmatched = {}, []
    for name in acled_names:
        if name in gadm_names:
            mapping[name] = name
            continue
        close = difflib.get_close_matches(name, gadm_list, n=1, cutoff=0.82)
        if close:
            mapping[name] = close[0]
        else:
            unmatched.append(name)
    return mapping, unmatched


def aggregate_conflict(acled, districts_gdf):
    acled = acled.copy()
    acled['event_date'] = pd.to_datetime(acled['event_date'])
    acled['year'] = acled['event_date'].dt.year
    acled['month'] = acled['event_date'].dt.month

    names = acled['admin2'].dropna().unique()
    gadm_names = set(districts_gdf['district'])
    mapping, unmatched = build_crosswalk_direct_fuzzy(names, gadm_names)
    acled['district'] = acled['admin2'].map(mapping)
    matched = acled.dropna(subset=['district']).copy()
    print(f'  crosswalk: {len(mapping)}/{len(names)} admin2 names matched, '
          f'{len(matched):,}/{len(acled):,} events retained')
    if unmatched:
        print(f'  unmatched (first 10): {sorted(unmatched)[:10]}')

    matched['is_pv'] = matched['event_type'].isin(cfg.POLITICAL_VIOLENCE)
    out = (matched.groupby(['district', 'year', 'month'])
           .agg(events=('event_type', 'count'), pv_events=('is_pv', 'sum'))
           .reset_index())
    out[['events', 'pv_events']] = out[['events', 'pv_events']].astype(int)
    return out


def last_complete_month_before(d):
    y, m = (d.year, d.month - 1) if d.month > 1 else (d.year - 1, 12)
    return y, m


def month_range(end_ym, n):
    y, m = end_ym
    months = []
    for _ in range(n):
        months.append((y, m))
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    return list(reversed(months))


def main():
    print(f'=== Round-2 scoping: {COUNTRY} (real boundaries, '
          f'{WINDOW_MONTHS}-month window) ===\n')

    ensure_shapefile()
    print('Building districts from GADM (township level)...')
    districts = build_myanmar_districts(SHP_PATH)
    print(f'  {len(districts)} townships\n')

    last_sp = sp_last_available()
    end_ym = last_complete_month_before(date(last_sp.year, last_sp.month, 1))
    months = month_range(end_ym, WINDOW_MONTHS)
    start = date(months[0][0], months[0][1], 1)
    end_last_y, end_last_m = months[-1]
    end_date = (date(end_last_y + (1 if end_last_m == 12 else 0),
                     1 if end_last_m == 12 else end_last_m + 1, 1) - timedelta(days=1))
    print(f'Window: {months[0][0]}-{months[0][1]:02d} .. {months[-1][0]}-{months[-1][1]:02d}\n')

    print('Fetching ACLED...')
    acled = fetch_acled_country(COUNTRY, start.isoformat())
    print(f'  {len(acled):,} raw events')
    conflict_pm = aggregate_conflict(acled, districts)
    print(f'  {len(conflict_pm):,} district-month conflict rows\n')

    print('Fetching FIRMS (VIIRS_SNPP_SP)...')
    fires = fetch_firms_range(BBOX, 'VIIRS_SNPP_SP', start, end_date)
    print(f'  {len(fires):,} detections')
    assigned = spatial.assign_points_to_districts(fires, districts).dropna(subset=['district'])
    fires_pm = spatial.aggregate_fires(assigned, districts)
    print(f'  {len(fires_pm):,} district-month fire rows (after boundary join)\n')

    print('Fetching ERA5 wind (CDS)...')
    cfg.BBOX = BBOX
    wind_grid = fetch_wind_months(months)
    district_wind = grid_to_district_wind(wind_grid, districts)
    print(f'  {len(district_wind):,} district-month wind rows\n')

    print('Building upwind instrument...')
    t0 = time.time()
    instrument_df = instr.build_instrument(assigned, district_wind, districts)
    print(f'  done in {time.time() - t0:.0f}s\n')

    all_ym = pd.DataFrame(months, columns=['year', 'month'])
    panel = pd.merge(districts[['district']], all_ym, how='cross')
    panel = panel.merge(conflict_pm, on=['district', 'year', 'month'], how='left')
    panel[['events', 'pv_events']] = panel[['events', 'pv_events']].fillna(0).astype(int)
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
    prov = dict(zip(districts['district'], districts['province']))
    panel['province'] = panel['district'].map(prov)

    print(f'Panel: {len(panel):,} district-months, {panel.district.nunique()} '
          f'townships, {panel.year_month.nunique()} months\n')

    print('Estimating...')
    fs = estimate.first_stage(panel)
    print(f'  first-stage F = {fs["f_stat"]:.1f}')
    full_iv = estimate.full_sample_iv(panel)
    for row in full_iv:
        print(f'  {row["label"]} ({row["outcome"]}, dl={row["distributed_lag"]}): '
              f'coef={row["coef"]:.4f} p={row["p"]:.3g}')
    thresholds = estimate.conflict_active_table(panel)
    print('\n  conflict-active heterogeneity:')
    for t in thresholds:
        print(f'    tau>={t["threshold"]:.0%}: {t["n_districts"]} districts, '
              f'events coef={t["events"]["coef"]:.4f} p={t["events"]["p"]:.3g}, '
              f'F={t["first_stage_F"]:.0f}')

    print('\n=== SUMMARY ===')
    print(f'{COUNTRY}: F={fs["f_stat"]:.1f}, {panel.district.nunique()} townships, '
          f'{len(panel):,} obs over {WINDOW_MONTHS} months.')
    print('Compare against Nigeria Round 2 (F=1,131.5, 775 LGAs, 24 months, '
          'directionally right but nothing significant - underpowered at '
          'that window length) before deciding whether to commit to the '
          'full historical build.')


if __name__ == '__main__':
    main()
