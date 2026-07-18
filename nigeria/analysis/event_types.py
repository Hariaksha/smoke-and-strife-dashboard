"""Event-type decomposition for the Nigeria panel: does fire exposure's
effect concentrate in Riots/Violence against civilians specifically, the
way the paper found for Indonesia (Section 5.5), rather than showing up
(or not) uniformly across the aggregate events/pv_events categories
tested in build_panel.py?

Reuses the already-built nigeria_panel.parquet (fires/wind/instrument
already computed - not re-fetched) and only re-pulls ACLED (~15s) to tag
events with the finer categories, merges those into the saved panel, and
re-estimates. No FIRMS/wind/instrument work, so this should take well
under 5 minutes, unlike the ~3.5 hour full build.

To run:
    source /Users/hariaksha/Documents/GitHub/smoke-and-strife-dashboard/secrets.env
    /Users/hariaksha/Documents/GitHub/climate-conflict/.venv/bin/python \\
        /Users/hariaksha/Documents/GitHub/smoke-and-strife-dashboard/nigeria/analysis/event_types.py
"""
import difflib
import io
import sys
from pathlib import Path

import pandas as pd
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # repo root
from pipeline import config as _cfg
from pipeline import spatial, estimate

HERE = Path(__file__).resolve().parents[1]
SHP = HERE / 'data/administrative/gadm41_NGA_shp/gadm41_NGA_2.shp'
PANEL_PARQUET = HERE / 'analysis' / 'nigeria_panel.parquet'

print('=' * 70)
print('NIGERIA EVENT-TYPE DECOMPOSITION (tau >= 30% conflict-active sample)')
print('=' * 70)

# ── 1. Load the already-built panel (no fires/wind/instrument rework) ──
print('\n[1] Loading saved panel...')
panel = pd.read_parquet(PANEL_PARQUET)
print(f'  {len(panel):,} district-months, {panel.district.nunique()} districts, '
      f'{panel.year_month.nunique()} months')

# ── 2. Districts (for the crosswalk's GADM name set) ────────────────────
print('\n[2] Districts from GADM...')
districts = spatial.build_districts_from_gadm(SHP)
print(f'  {len(districts)} LGAs loaded')

# ── 3. Re-fetch ACLED and re-run the crosswalk (same logic as build_panel.py) ──
print('\n[3] ACLED conflict data...')


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


def build_crosswalk_direct_fuzzy(acled_names, gadm_names):
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


acled = fetch_acled_country('Nigeria', '2015-01-01')
print(f'  {len(acled):,} events fetched ({acled.event_date.min()} to {acled.event_date.max()})')
acled['event_date'] = pd.to_datetime(acled['event_date'])
acled['year'] = acled['event_date'].dt.year
acled['month'] = acled['event_date'].dt.month
names = acled['admin2'].dropna().unique()
gadm_names = set(districts['district'])
mapping, unmatched = build_crosswalk_direct_fuzzy(names, gadm_names)
acled['district'] = acled['admin2'].map(mapping)
matched = acled.dropna(subset=['district']).copy()
print(f'  {len(matched):,}/{len(acled):,} events retained after crosswalk '
      f'({len(mapping)}/{len(names)} admin2 names matched)')

# ── 4. Tag the finer event-type categories (same definitions as the
#      Indonesia pipeline: pipeline/config.py RIOTS_PROTESTS, BATTLES_
#      VIOLENCE, FOURWAY_TYPES) and aggregate to district-month ──────────
print('\n[4] Tagging event types...')
matched['is_riots_protests'] = matched['event_type'].isin(_cfg.RIOTS_PROTESTS)
matched['is_battles'] = matched['event_type'].isin(_cfg.BATTLES_VIOLENCE)
for col, t in _cfg.FOURWAY_TYPES.items():
    matched[f'is_{col}'] = matched['event_type'] == t

for col, t in _cfg.FOURWAY_TYPES.items():
    n = matched[f'is_{col}'].sum()
    print(f'  {t:30s}: {n:,} ({n/len(matched)*100:.1f}%)')

agg = {'riots_protests': ('is_riots_protests', 'sum'),
       'battles_violence': ('is_battles', 'sum')}
for col in _cfg.FOURWAY_TYPES:
    agg[col] = (f'is_{col}', 'sum')
event_type_pm = matched.groupby(['district', 'year', 'month']).agg(**agg).reset_index()

# ── 5. Merge into the saved panel (left join, missing -> 0) ────────────
print('\n[5] Merging into saved panel...')
new_cols = ['riots_protests', 'battles_violence'] + list(_cfg.FOURWAY_TYPES)
panel = panel.merge(event_type_pm, on=['district', 'year', 'month'], how='left')
for c in new_cols:
    panel[c] = panel[c].fillna(0).astype(int)
panel.to_parquet(PANEL_PARQUET, index=False)
print(f'  Saved (with event-type columns added): {PANEL_PARQUET}')

# ── 6. Estimate: same tau>=30% subsample the paper's decomposition used ─
print('\n[6] Estimation (tau >= 30% conflict-active subsample)...')
twoway, fourway = estimate.event_type_tables(panel)


def _stars(p):
    return '***' if p < 0.01 else '**' if p < 0.05 else '*' if p < 0.1 else ''


sub30 = panel[panel.district.isin(
    panel.groupby('district')['events'].apply(lambda s: (s > 0).sum())
    .loc[lambda s: s >= 0.30 * panel['year_month'].nunique()].index)]
means = {c: sub30[c].mean() for c in new_cols}

print('\n  Two-way composite categories:')
for col, row in twoway.items():
    pct = row['coef'] / means[col] * 100 if means[col] > 0 else float('nan')
    print(f'    {row["label"]:20s} coef={row["coef"]:+.4f}{_stars(row["p"])} '
          f'(se {row["se"]:.4f}, p={row["p"]:.3f})  ~{pct:+.0f}% of mean')

print('\n  Four-way literal ACLED categories (the paper\'s finer cut):')
for col, row in fourway.items():
    pct = row['coef'] / means[col] * 100 if means[col] > 0 else float('nan')
    print(f'    {row["label"]:28s} coef={row["coef"]:+.4f}{_stars(row["p"])} '
          f'(se {row["se"]:.4f}, p={row["p"]:.3f})  ~{pct:+.0f}% of mean')

print('\nDone.')
