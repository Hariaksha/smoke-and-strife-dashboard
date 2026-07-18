"""Placebo/exclusion-restriction test for the Nigeria panel, mirroring the
Indonesia paper's own check (Section 5.6 / Appendix C.3): replace each
district's real upwind instrument with a randomly-assigned, implausibly
distant (>=500km) district's instrument, then re-run the event-type IVs
that showed significance in the real test (Protests, Riots, Strategic
developments). If the instrument is doing what it's supposed to - picking
up genuine wind-transported fire exposure, not some spurious shared
regional pattern - the placebo version should show (a) little to no
first-stage power (a district 500km+ away has no physical mechanism to
affect local fire intensity) and (b) small, statistically insignificant
effects on conflict.

Design choice: a FIXED random permutation (each district gets one
randomly-assigned, >=500km-distant "donor" district whose instrument
values it borrows across all months), not a fresh per-month draw - this
tests whether some persistent per-district confound could be driving the
real result, which is the more standard reading of "randomly-assigned...
district's instrument" as a robustness check.

Uses the already-saved nigeria_panel.parquet - no new data fetching, so
this should run in well under a minute.

To run:
    source /Users/hariaksha/Documents/GitHub/smoke-and-strife-dashboard/secrets.env
    /Users/hariaksha/Documents/GitHub/climate-conflict/.venv/bin/python \\
        /Users/hariaksha/Documents/GitHub/smoke-and-strife-dashboard/nigeria/analysis/placebo_test.py
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pyfixest as pf

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # repo root
from pipeline import spatial
from pipeline.instrument import haversine_vectorised

HERE = Path(__file__).resolve().parents[1]
SHP = HERE / 'data/administrative/gadm41_NGA_shp/gadm41_NGA_2.shp'
PANEL_PARQUET = HERE / 'analysis' / 'nigeria_panel.parquet'
MIN_DISTANCE_KM = 500
SEED = 42
FE = 'district + year_month'
VCOV = {'CRV1': 'province'}

print('=' * 70)
print('NIGERIA PLACEBO TEST: instrument swapped for a >=500km-distant district')
print('=' * 70)

# ── 1. Load panel + districts ────────────────────────────────────────────
print('\n[1] Loading panel and districts...')
panel = pd.read_parquet(PANEL_PARQUET)
districts = spatial.build_districts_from_gadm(SHP)
print(f'  {len(panel):,} district-months, {panel.district.nunique()} districts')

# ── 2. Pairwise distances -> a >=500km-distant "donor" district for each ─
print(f'\n[2] Building >={MIN_DISTANCE_KM}km donor assignment...')
centroids = districts.set_index('district')[['dist_lat', 'dist_lon']]
names = centroids.index.to_numpy()
lats, lons = centroids['dist_lat'].to_numpy(), centroids['dist_lon'].to_numpy()

rng = np.random.default_rng(SEED)
donor = {}
for i, name in enumerate(names):
    d = haversine_vectorised(lats[i], lons[i], lats, lons)
    eligible = names[(d >= MIN_DISTANCE_KM) & (names != name)]
    if len(eligible) == 0:
        continue  # shouldn't happen in a country this size, but be safe
    donor[name] = rng.choice(eligible)

n_no_donor = panel['district'].nunique() - len(donor)
print(f'  {len(donor)}/{districts.district.nunique()} districts assigned a donor '
      f'({n_no_donor} could not be, if any)')
print(f'  sample assignments: {list(donor.items())[:5]}')

# ── 3. Build the placebo instrument: each district borrows its donor's
#      log_upwind_frp (and lag) for the same year-month ─────────────────
print('\n[3] Constructing placebo instrument...')
donor_lookup = panel[['district', 'year', 'month', 'log_upwind_frp', 'log_upwind_frp_l1']].rename(
    columns={'district': 'donor_district',
             'log_upwind_frp': 'placebo_log_upwind_frp',
             'log_upwind_frp_l1': 'placebo_log_upwind_frp_l1'})
panel['donor_district'] = panel['district'].map(donor)
panel = panel.merge(donor_lookup, on=['donor_district', 'year', 'month'], how='left')
missing = panel['placebo_log_upwind_frp'].isna().sum()
print(f'  {missing:,} rows without a placebo value (donor missing that month) - dropped from placebo regressions')
placebo_panel = panel.dropna(subset=['placebo_log_upwind_frp', 'placebo_log_upwind_frp_l1']).copy()

# ── 4. First stage: does the placebo instrument predict LOCAL fire
#      intensity at all? (It shouldn't - no physical channel at 500km+.) ─
print('\n[4] Placebo first stage (local log_frp ~ placebo instrument)...')
fs_real = pf.feols(f'log_frp ~ log_upwind_frp + log_upwind_frp_l1 | {FE}',
                   data=placebo_panel, vcov=VCOV)
fs_placebo = pf.feols(
    f'log_frp ~ placebo_log_upwind_frp + placebo_log_upwind_frp_l1 | {FE}',
    data=placebo_panel, vcov=VCOV)


def _f_stat(res, k):
    r2w, n = res._r2_within, res._N
    return (r2w / k) / ((1 - r2w) / (n - k - 1))


print(f'  Real instrument,    same rows: F = {_f_stat(fs_real, 2):.1f}')
print(f'  Placebo instrument, same rows: F = {_f_stat(fs_placebo, 2):.1f} '
      f'(expected: near zero, if the exclusion restriction holds)')

# ── 5. Second stage: re-run the categories that were significant in the
#      real event-type decomposition, using the placebo instrument ──────
print('\n[5] Placebo second stage (event-type IVs, tau >= 30% subsample)...')
active = placebo_panel.groupby('district')['events'].apply(lambda s: (s > 0).sum())
n_months = placebo_panel['year_month'].nunique()
sub30 = placebo_panel[placebo_panel['district'].isin(
    active[active >= 0.30 * n_months].index)]
print(f'  {sub30.district.nunique()} districts, {len(sub30):,} obs')


def _stars(p):
    return '***' if p < 0.01 else '**' if p < 0.05 else '*' if p < 0.1 else ''


CATS = ['protests', 'riots', 'violence_against_civilians', 'strategic_developments',
        'riots_protests', 'battles_violence']
print(f'\n  {"Category":28s} {"real coef (p)":22s} {"placebo coef (p)":22s}')
for col in CATS:
    # Matches pipeline/estimate.py's _iv() exactly: log_frp instrumented
    # jointly by the contemporaneous + lagged instrument (IV_TERMS), with
    # log_frp_l1 as an uninstrumented control (CONTROLS).
    real = pf.feols(f'{col} ~ log_frp_l1 | {FE} | log_frp ~ log_upwind_frp + log_upwind_frp_l1',
                    data=sub30, vcov=VCOV)
    real_row = real.tidy().reset_index()
    real_row = real_row[real_row['Coefficient'] == 'log_frp'].iloc[0]

    placebo = pf.feols(
        f'{col} ~ log_frp_l1 | {FE} | log_frp ~ placebo_log_upwind_frp + placebo_log_upwind_frp_l1',
        data=sub30, vcov=VCOV)
    placebo_row = placebo.tidy().reset_index()
    placebo_row = placebo_row[placebo_row['Coefficient'] == 'log_frp'].iloc[0]

    real_str = f'{real_row["Estimate"]:+.4f}{_stars(real_row["Pr(>|t|)"])} (p={real_row["Pr(>|t|)"]:.3f})'
    placebo_str = f'{placebo_row["Estimate"]:+.4f}{_stars(placebo_row["Pr(>|t|)"])} (p={placebo_row["Pr(>|t|)"]:.3f})'
    print(f'  {col:28s} {real_str:22s} {placebo_str:22s}')

print('\nDone. Exclusion restriction is supported if placebo effects are')
print('small, inconsistent in sign, and statistically insignificant')
print('relative to the real (donor-instrumented) effects above.')
