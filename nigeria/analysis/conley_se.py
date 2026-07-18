"""Conley spatial-correlation standard errors for the Nigeria event-type
IVs, mirroring the Indonesia paper's own check (Section 5.6/Appendix C.3):
allow residual correlation across province boundaries, more conservative
than the province-clustered SEs used everywhere else in this project.

Uses econtools's Spatial HAC (SHAC) estimator - the only maintained Python
package found with native 2SLS + Conley SE support (pyfixest has no such
option). Two things worth knowing about it before trusting these numbers:

  1. Its distance calculation (econtools.metrics.core._shac_weights) is
     naive Euclidean distance in raw lon/lat DEGREES, not true geodesic
     (great-circle) distance. Near the equator (Nigeria spans ~4-14N) this
     is a modest ~1-3% distortion, not a severe one, but it's not exact.
     Bandwidths below are specified in km and converted via the standard
     ~111 km/degree approximation, which itself is only exact at the
     equator.
  2. `econtools.metrics.ivreg`'s `fe_name` only absorbs ONE fixed-effect
     dimension (single-group demeaning), not the district+year_month
     two-way FE the real spec needs. This script manually two-way demeans
     (district, then year_month on the residuals) before calling ivreg.
     That sequential demeaning is EXACT (not an approximation) because the
     tau>=30% subsample is a perfectly balanced panel (117 districts x
     127 months = 14,859 - verified below), which is the condition under
     which one-pass sequential demeaning equals genuine two-way FE.

Before trusting the SHAC standard errors, this script first validates
that the manually-demeaned 2SLS point estimates reproduce pyfixest's
already-established coefficients (from event_types.py) almost exactly -
if that validation fails, something is wrong with the demeaning, and the
standard errors that follow shouldn't be trusted either.

Restricted to the tau>=30% conflict-active subsample (N=14,859), not the
full 88,138-row panel: the SHAC estimator is O(N^2), so the full panel
would be ~35x more expensive - and tau>=30% is where the actual findings
live anyway (matching the paper's own scope for this check).

To run:
    source /Users/hariaksha/Documents/GitHub/smoke-and-strife-dashboard/secrets.env
    /Users/hariaksha/Documents/GitHub/climate-conflict/.venv/bin/python \\
        /Users/hariaksha/Documents/GitHub/smoke-and-strife-dashboard/nigeria/analysis/conley_se.py
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pyfixest as pf

# econtools (as of the installed version) calls the old top-level
# pd.value_counts(series) API internally (metrics/core.py:df_cluster),
# which pandas removed in 2.0+ (only Series.value_counts() remains). This
# restores it as a compatibility shim rather than patching the vendored
# package - remove if a fixed econtools release drops this dependency.
if not hasattr(pd, 'value_counts'):
    pd.value_counts = lambda s, *a, **k: s.value_counts(*a, **k)

from econtools.metrics import ivreg

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # repo root
from pipeline import spatial

HERE = Path(__file__).resolve().parents[1]
SHP = HERE / 'data/administrative/gadm41_NGA_shp/gadm41_NGA_2.shp'
PANEL_PARQUET = HERE / 'analysis' / 'nigeria_panel.parquet'
KM_PER_DEGREE = 111.0  # equatorial approximation; see module docstring
BANDWIDTHS_KM = [100, 200, 500]
FE = 'district + year_month'
VCOV = {'CRV1': 'province'}
CATS = ['protests', 'riots', 'violence_against_civilians', 'strategic_developments',
        'riots_protests', 'battles_violence']


def _stars(p):
    return '***' if p < 0.01 else '**' if p < 0.05 else '*' if p < 0.1 else ''


def two_way_demean(df, cols, unit_col='district', time_col='year_month'):
    """Sequential demeaning: by unit, then by time on the residuals.
    Exact two-way FE for a balanced panel (verified by the caller)."""
    out = df[cols].copy()
    out = out - df.groupby(unit_col)[cols].transform('mean')
    out = out - out.groupby(df[time_col])[cols].transform('mean')
    return out


print('=' * 70)
print('NIGERIA CONLEY SPATIAL-CORRELATION STANDARD ERRORS')
print('=' * 70)

# ── 1. Load panel + district centroids ──────────────────────────────────
print('\n[1] Loading panel and district centroids...')
panel = pd.read_parquet(PANEL_PARQUET)
districts = spatial.build_districts_from_gadm(SHP)
centroids = districts.set_index('district')[['dist_lat', 'dist_lon']]
panel = panel.merge(centroids, left_on='district', right_index=True, how='left')

active = panel.groupby('district')['events'].apply(lambda s: (s > 0).sum())
n_months = panel['year_month'].nunique()
sub30 = panel[panel['district'].isin(active[active >= 0.30 * n_months].index)].copy()
n_districts, n_months30 = sub30.district.nunique(), sub30.year_month.nunique()
is_balanced = len(sub30) == n_districts * n_months30
print(f'  tau>=30% subsample: {len(sub30):,} obs, {n_districts} districts, '
      f'{n_months30} months (balanced: {is_balanced})')
if not is_balanced:
    print('  WARNING: not balanced - sequential demeaning below is only an')
    print('  approximation to true two-way FE, not exact. Proceed with caution.')

# ── 2. Two-way demean everything the regression needs ───────────────────
print('\n[2] Two-way demeaning (district, then year_month)...')
regvars = ['log_frp', 'log_upwind_frp', 'log_upwind_frp_l1', 'log_frp_l1'] + CATS
demeaned = two_way_demean(sub30, regvars)
demeaned.columns = [f'{c}_dm' for c in demeaned.columns]
sub30 = pd.concat([sub30.reset_index(drop=True), demeaned.reset_index(drop=True)], axis=1)

# ── 3. Validate against pyfixest before trusting anything downstream ────
print('\n[3] Validating: does the manual two-way demean reproduce pyfixest?')
check_col = 'strategic_developments'
pfx = pf.feols(f'{check_col} ~ log_frp_l1 | {FE} | log_frp ~ log_upwind_frp + log_upwind_frp_l1',
              data=sub30, vcov=VCOV)
pfx_row = pfx.tidy().reset_index()
pfx_coef = pfx_row[pfx_row['Coefficient'] == 'log_frp'].iloc[0]['Estimate']

et_check = ivreg(sub30, y_name=f'{check_col}_dm', x_name='log_frp_dm',
                 z_name=['log_upwind_frp_dm', 'log_upwind_frp_l1_dm'],
                 w_name='log_frp_l1_dm', nocons=True, vce_type='cluster',
                 cluster='province')
et_coef = et_check.beta['log_frp_dm']
diff = abs(pfx_coef - et_coef)
print(f'  pyfixest coef (real spec, from event_types.py): {pfx_coef:+.6f}')
print(f'  econtools coef (two-way-demeaned):               {et_coef:+.6f}')
print(f'  difference: {diff:.6f} {"OK - matches" if diff < 1e-4 else "MISMATCH - do not trust results below"}')
if diff >= 1e-4:
    print('\n  Stopping: demeaning does not reproduce the known-correct coefficient.')
    sys.exit(1)

# ── 4. Conley SEs across a few bandwidths, for every category ───────────
print('\n[4] Conley (SHAC) standard errors, triangle kernel, multiple bandwidths...')
for band_km in BANDWIDTHS_KM:
    band_deg = band_km / KM_PER_DEGREE
    print(f'\n  --- bandwidth = {band_km} km ({band_deg:.3f} deg) ---')
    print(f'  {"Category":28s} {"coef":>10s} {"province-clustered p":>22s} {"Conley (SHAC) p":>18s}')
    for col in CATS:
        res = ivreg(sub30, y_name=f'{col}_dm', x_name='log_frp_dm',
                    z_name=['log_upwind_frp_dm', 'log_upwind_frp_l1_dm'],
                    w_name='log_frp_l1_dm', nocons=True,
                    vce_type='shac',
                    shac={'x': 'dist_lon', 'y': 'dist_lat', 'kern': 'tria', 'band': band_deg})
        coef = res.beta['log_frp_dm']
        p = res.pt['log_frp_dm']
        clustered = pf.feols(f'{col} ~ log_frp_l1 | {FE} | log_frp ~ log_upwind_frp + log_upwind_frp_l1',
                             data=sub30, vcov=VCOV)
        cl_row = clustered.tidy().reset_index()
        cl_p = cl_row[cl_row['Coefficient'] == 'log_frp'].iloc[0]['Pr(>|t|)']
        print(f'  {col:28s} {coef:+10.4f} {cl_p:>21.3f}{_stars(cl_p):3s} {p:>17.3f}{_stars(p)}')

print('\nDone.')
