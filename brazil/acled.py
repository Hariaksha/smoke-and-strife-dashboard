"""Brazil ACLED fetch + crosswalk + event-type aggregation, mirroring
nigeria/acled.py exactly - ACLED reports admin1/admin2 names in English
regardless of country, so the same direct+fuzzy crosswalk approach that
worked for Nigeria's LGAs works for Brazil's municipalities.
"""
import difflib
import io

import pandas as pd
import requests

from . import config as cfg


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
    return pd.concat(frames, ignore_index=True)


def build_crosswalk_direct_fuzzy(acled_names, gadm_names):
    """English ACLED admin2 -> English GADM municipality: direct match,
    then a fuzzy fallback (difflib) for spelling variants."""
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


def aggregate_conflict_with_event_types(acled, districts_gdf, verbose=True):
    """ACLED event-level frame -> district-month outcome counts, including
    the finer event-type columns (protests, riots, violence_against_civilians,
    strategic_developments, riots_protests, battles_violence).

    Expects columns: event_date, admin2, event_type, fatalities.
    """
    acled = acled.copy()
    acled['event_date'] = pd.to_datetime(acled['event_date'])
    acled['year'] = acled['event_date'].dt.year
    acled['month'] = acled['event_date'].dt.month

    names = acled['admin2'].dropna().unique()
    gadm_names = set(districts_gdf['district'])
    mapping, unmatched = build_crosswalk_direct_fuzzy(names, gadm_names)
    acled['district'] = acled['admin2'].map(mapping)
    matched = acled.dropna(subset=['district']).copy()
    if verbose:
        print(f'  crosswalk: {len(mapping)}/{len(names)} admin2 names matched, '
              f'{len(matched):,}/{len(acled):,} events retained')

    matched['is_pv'] = matched['event_type'].isin(cfg.POLITICAL_VIOLENCE)
    matched['is_riots_protests'] = matched['event_type'].isin(cfg.RIOTS_PROTESTS)
    matched['is_battles'] = matched['event_type'].isin(cfg.BATTLES_VIOLENCE)
    for col, t in cfg.FOURWAY_TYPES.items():
        matched[f'is_{col}'] = matched['event_type'] == t

    agg = {
        'events': ('event_type', 'count'),
        'pv_events': ('is_pv', 'sum'),
        'fatalities': ('fatalities', 'sum'),
        'riots_protests': ('is_riots_protests', 'sum'),
        'battles_violence': ('is_battles', 'sum'),
    }
    for col in cfg.FOURWAY_TYPES:
        agg[col] = (f'is_{col}', 'sum')

    out = matched.groupby(['district', 'year', 'month']).agg(**agg).reset_index()
    count_cols = [c for c in out.columns if c not in ('district', 'year', 'month')]
    out[count_cols] = out[count_cols].astype(int)
    return out
