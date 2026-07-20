"""Round-2 scoping for Brazil: real GADM municipality boundaries + a real
ACLED pull, over a ~24-month window (not the full history) - the same
"dress rehearsal" step Nigeria's and Myanmar's own scoping did before
committing to a full multi-year build (see nigeria/README.md's "Round 2"
and the Myanmar equivalent).

Round 1 (scoping/gutcheck.py) already confirmed the upwind-fire
instrument has real power in Brazil (F=774.6, coarse 1-degree grid, no
real boundaries, no conflict). This round checks two more things at
lower cost than the full historical build:
  1. Does the instrument still hold with real municipalities instead of
     a 1-degree grid?
  2. Does ACLED's conflict data crosswalk cleanly onto GADM municipality
     names, and does the conflict-active heterogeneity shape look
     anything like Indonesia's/Nigeria's?

Unlike Myanmar, Brazil's GADM hierarchy is only two levels deep (State ->
Municipality, confirmed by inspection: level 1 = 27 states, level 2 =
5,572 municipalities) - the same standard shape as Nigeria/Indonesia, so
this reuses pipeline.spatial.build_districts_from_gadm directly with no
custom district-builder needed.

Known cost concern (already flagged when Brazil was first considered):
5,572 municipalities is ~7x Nigeria's 775 LGAs and ~19x Myanmar's 286
townships. This 24-month Round 2 should still be tractable, but the
instrument-construction step will likely take noticeably longer than
Myanmar's 83 seconds - budget up to 20-30 minutes for that step alone,
and treat this run's total wall-clock time as the real first data point
on whether a full historical build is even worth attempting.

Downloads Brazil's GADM 4.1 shapefile itself on first run - this one is
big (~210 MB zipped, all of Brazil's municipal boundaries), so the
download step alone may take a couple of minutes depending on connection
speed.

Run (from repo root):
    source /Users/hariaksha/Documents/GitHub/climate-conflict/.venv/bin/activate
    source secrets.env
    python -m scoping.brazil_round2

Needs ACLED_EMAIL, ACLED_PASSWORD, FIRMS_MAP_KEY, CDSAPI_KEY (all four -
this round tests the conflict crosswalk too, unlike Round 1).
"""
import io
import time
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
from scoping.myanmar_round2 import (
    fetch_acled_country, aggregate_conflict, last_complete_month_before, month_range,
)

HERE = Path(__file__).resolve().parent
DATA_DIR = HERE / 'brazil_data'
SHP_DIR = DATA_DIR / 'gadm41_BRA_shp'
SHP_PATH = SHP_DIR / 'gadm41_BRA_2.shp'
GADM_URL = 'https://geodata.ucdavis.edu/gadm/gadm4.1/shp/gadm41_BRA_shp.zip'

BBOX = (-74.0, -34.0, -34.0, 5.5)  # west, south, east, north
COUNTRY = 'Brazil'
WINDOW_MONTHS = 24


def ensure_shapefile():
    if SHP_PATH.exists():
        print(f'GADM shapefile already present at {SHP_PATH}')
        return
    import zipfile
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    zip_path = DATA_DIR / 'gadm41_BRA_shp.zip'
    print(f'Downloading {GADM_URL} (~210 MB, may take a couple of minutes)...')
    r = requests.get(GADM_URL, timeout=600)
    r.raise_for_status()
    zip_path.write_bytes(r.content)
    print(f'  {len(r.content) / 1e6:.1f} MB downloaded, extracting...')
    SHP_DIR.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as z:
        z.extractall(SHP_DIR)
    zip_path.unlink()
    print(f'  extracted to {SHP_DIR}')


def main():
    print(f'=== Round-2 scoping: {COUNTRY} (real boundaries, '
          f'{WINDOW_MONTHS}-month window) ===\n')

    ensure_shapefile()
    print('Building districts from GADM (municipality level)...')
    districts = spatial.build_districts_from_gadm(SHP_PATH)
    print(f'  {len(districts)} municipalities\n')

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

    print('Building upwind instrument (this is the step to watch - '
          '5,572 municipalities is much bigger than Myanmar/Nigeria)...')
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
          f'municipalities, {panel.year_month.nunique()} months\n')

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
    print(f'{COUNTRY}: F={fs["f_stat"]:.1f}, {panel.district.nunique()} municipalities, '
          f'{len(panel):,} obs over {WINDOW_MONTHS} months.')
    print('Compare against Nigeria Round 2 (F=1,131.5, 775 LGAs, weak/consistent-signed, '
          'held up in the full build) and Myanmar Round 2 (F=96.6, but sign/significance '
          'flips depending on specification - did not hold up cleanly) before deciding '
          'whether to commit to the full historical build.')


if __name__ == '__main__':
    main()
