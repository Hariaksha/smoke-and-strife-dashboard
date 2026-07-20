"""Round-3 scoping for Brazil: the full historical window (2015-01 through
the last complete month - the same ~127-month span Indonesia's paper and
Nigeria's own build used), not just Round 2's 24-month dress rehearsal.

Round 2 (scoping/brazil_round2.py) found a strong, stable instrument
(F=16,041.6 overall, never below F=786 even in the thinnest conflict-active
subsample) and a clean monotonic gradient shape (coefficient magnitude
rising with tau) matching Indonesia's own headline pattern - just with the
opposite sign (positive: more fire, more conflict, plausible for Brazil
given fire there is tied to land-clearing/deforestation conflict, a
different mechanism than Indonesia's/Nigeria's smoke-suppresses-collective-
action story) and not yet significant at 24 months. This round checks
whether that gradient sharpens into something significant with the full
decade, the same way Nigeria's own Round 2 (weak instrument, right shape,
nothing significant) turned into a real finding once Round 3 added the
full history.

COST WARNING - read before running: Round 2's instrument-construction
step alone took 523s (~8.7 min) for a 24-month window with ~4M fire
detections. The full window is ~127 months (~5.5x) and Brazil's fire
volume will scale up too (not necessarily linearly - KD-tree radius-query
cost scales worse than linearly with point density, which is exactly why
Nigeria's own full build took 3+ hours against an original 10-20 min
estimate). Budget for this taking multiple hours, plausibly the most
expensive computation run in this whole project. Consider running it
somewhere that survives your terminal closing (nohup, tmux, or just
leaving the laptop alone and awake) rather than a normal foreground
terminal session you might close.

To make an interruption survivable, every fetch stage is checkpointed to
its own parquet file in scoping/brazil_data/ before the expensive
instrument-construction step runs. Re-running this script after a crash
skips any stage whose output file already exists, so a failure during
(say) the FIRMS fetch doesn't force re-fetching ACLED too, and a failure
during instrument construction (the real risk) doesn't force re-fetching
anything at all.

Run (from repo root):
    source /Users/hariaksha/Documents/GitHub/climate-conflict/.venv/bin/activate
    source secrets.env
    python -m scoping.brazil_round3

Needs ACLED_EMAIL, ACLED_PASSWORD, FIRMS_MAP_KEY, CDSAPI_KEY (all four).
"""
import time
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import requests

from pipeline import config as cfg
from pipeline import spatial
from pipeline import instrument as instr
from pipeline import estimate
from pipeline.fetch_wind import fetch_wind_months, grid_to_district_wind
from scoping.gutcheck import fetch_firms_range, sp_last_available
from scoping.myanmar_round2 import fetch_acled_country, aggregate_conflict, last_complete_month_before

HERE = Path(__file__).resolve().parent
DATA_DIR = HERE / 'brazil_data'
SHP_DIR = DATA_DIR / 'gadm41_BRA_shp'
SHP_PATH = SHP_DIR / 'gadm41_BRA_2.shp'
GADM_URL = 'https://geodata.ucdavis.edu/gadm/gadm4.1/shp/gadm41_BRA_shp.zip'

# checkpoint artifacts - each stage skips its own fetch if its file exists
CONFLICT_PARQUET = DATA_DIR / 'conflict_pm_full.parquet'
FIRES_RAW_PARQUET = DATA_DIR / 'fires_assigned_full.parquet'
FIRES_PM_PARQUET = DATA_DIR / 'fires_pm_full.parquet'
WIND_PARQUET = DATA_DIR / 'district_wind_full.parquet'
INSTRUMENT_PARQUET = DATA_DIR / 'instrument_full.parquet'

BBOX = (-74.0, -34.0, -34.0, 5.5)  # west, south, east, north
COUNTRY = 'Brazil'
FETCH_START_YM = 201412  # one month early, needed for Jan 2015's lag
PANEL_START_YM = 201501


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


def month_end_date(y, m):
    ny, nm = (y + 1, 1) if m == 12 else (y, m + 1)
    return date(ny, nm, 1) - timedelta(days=1)


def main():
    t_start = time.time()
    print(f'=== Round-3 scoping: {COUNTRY} (full historical window) ===\n')
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    ensure_shapefile()
    print('Building districts from GADM (municipality level)...')
    districts = spatial.build_districts_from_gadm(SHP_PATH)
    print(f'  {len(districts)} municipalities\n')

    last_sp = sp_last_available()
    end_ym_tuple = last_complete_month_before(date(last_sp.year, last_sp.month, 1))
    end_ym = end_ym_tuple[0] * 100 + end_ym_tuple[1]
    months = list(cfg.ym_iter(FETCH_START_YM, end_ym))
    print(f'Window: {FETCH_START_YM} .. {end_ym} ({len(months)} months)\n')

    # ── ACLED (checkpointed) ────────────────────────────────────────────
    if CONFLICT_PARQUET.exists():
        print(f'Conflict data already fetched, loading {CONFLICT_PARQUET.name}')
        conflict_pm = pd.read_parquet(CONFLICT_PARQUET)
    else:
        print('Fetching ACLED (full history)...')
        start = date(FETCH_START_YM // 100, FETCH_START_YM % 100, 1)
        acled = fetch_acled_country(COUNTRY, start.isoformat())
        print(f'  {len(acled):,} raw events')
        conflict_pm = aggregate_conflict(acled, districts)
        conflict_pm.to_parquet(CONFLICT_PARQUET, index=False)
    print(f'  {len(conflict_pm):,} district-month conflict rows\n')

    # ── FIRMS (checkpointed) ────────────────────────────────────────────
    if FIRES_PM_PARQUET.exists() and FIRES_RAW_PARQUET.exists():
        print(f'Fire data already fetched, loading {FIRES_PM_PARQUET.name}')
        fires_pm = pd.read_parquet(FIRES_PM_PARQUET)
        assigned = pd.read_parquet(FIRES_RAW_PARQUET)
    else:
        print('Fetching FIRMS (VIIRS_SNPP_SP, full history - this is slow, '
              'a decade in <=5-day windows)...')
        start = date(FETCH_START_YM // 100, FETCH_START_YM % 100, 1)
        end_date = month_end_date(*end_ym_tuple)
        fires = fetch_firms_range(BBOX, 'VIIRS_SNPP_SP', start, end_date)
        print(f'  {len(fires):,} detections')
        assigned = spatial.assign_points_to_districts(fires, districts).dropna(subset=['district'])
        assigned.to_parquet(FIRES_RAW_PARQUET, index=False)
        fires_pm = spatial.aggregate_fires(assigned, districts)
        fires_pm.to_parquet(FIRES_PM_PARQUET, index=False)
    print(f'  {len(fires_pm):,} district-month fire rows (after boundary join)\n')

    # ── ERA5 wind (checkpointed) ─────────────────────────────────────────
    if WIND_PARQUET.exists():
        print(f'Wind data already fetched, loading {WIND_PARQUET.name}')
        district_wind = pd.read_parquet(WIND_PARQUET)
    else:
        print('Fetching ERA5 wind (CDS, full history)...')
        cfg.BBOX = BBOX
        wind_grid = fetch_wind_months(months)
        district_wind = grid_to_district_wind(wind_grid, districts)
        district_wind.to_parquet(WIND_PARQUET, index=False)
    print(f'  {len(district_wind):,} district-month wind rows\n')

    # ── instrument (checkpointed - this is the expensive step) ──────────
    if INSTRUMENT_PARQUET.exists():
        print(f'Instrument already built, loading {INSTRUMENT_PARQUET.name}')
        instrument_df = pd.read_parquet(INSTRUMENT_PARQUET)
    else:
        print('Building upwind instrument (THE expensive step - budget '
              'multiple hours; progress prints every 10,000 rows)...')
        t0 = time.time()
        instrument_df = instr.build_instrument(assigned, district_wind, districts)
        print(f'  done in {time.time() - t0:.0f}s')
        instrument_df.to_parquet(INSTRUMENT_PARQUET, index=False)
    print()

    # ── panel + estimation ────────────────────────────────────────────────
    all_ym = pd.DataFrame(months, columns=['year', 'month'])
    panel = pd.merge(districts[['district']], all_ym, how='cross')
    panel = panel.merge(conflict_pm, on=['district', 'year', 'month'], how='left')
    conflict_cols = [c for c in conflict_pm.columns if c not in ('district', 'year', 'month')]
    panel[conflict_cols] = panel[conflict_cols].fillna(0).astype(int)
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
    panel = panel[panel['year_month'] >= PANEL_START_YM].reset_index(drop=True)
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
    print('\n  event-type decomposition (tau>=30%):')
    twoway, fourway = estimate.event_type_tables(panel)
    for col, row in {**fourway, **twoway}.items():
        print(f'    {row["label"]}: coef={row["coef"]:.4f} se={row["se"]:.4f} p={row["p"]:.3g}')

    print('\n=== SUMMARY ===')
    print(f'{COUNTRY}: F={fs["f_stat"]:.1f}, {panel.district.nunique()} municipalities, '
          f'{len(panel):,} obs over {panel.year_month.nunique()} months. '
          f'Total wall-clock time: {(time.time() - t_start) / 60:.1f} min.')


if __name__ == '__main__':
    main()
