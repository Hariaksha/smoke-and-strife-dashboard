"""Fetch ACLED conflict events for Indonesia via the ACLED API.

Auth: OAuth password grant against the myACLED account
(https://acleddata.com/api-documentation/getting-started).
The full Indonesia pull since 2015 is only ~20-25k events, so every run
re-fetches the complete window; this also picks up ACLED's weekly
historical revisions for free.

IMPORTANT - Research-tier latency: disaggregated (event-level, with lat/lon)
data is only available to Research myACLED accounts with a 12-month rolling
delay ("latent event data" - https://acleddata.com/faq-codebook-tools).
ACLED's *unlagged* weekly aggregated files exist but are country-year /
country-month-year only (https://acleddata.com/conflict-data/download-data-
files/aggregated-data) - far too coarse for a 447-district panel, so they
can't substitute here. Practical effect: `conflict_pm`'s max month, and
therefore the whole panel's end date (run_update.build_results takes the
min across sources), will sit ~12 months behind the fire/wind data unless
the account is upgraded to Partner or Enterprise tier (contact
access@acleddata.com / licensing@acleddata.com) for weekly disaggregated
data - see README.md.
"""
import io

import pandas as pd
import requests

from . import config

FIELDS = 'event_date|admin1|admin2|event_type|fatalities|latitude|longitude'
PAGE_SIZE = 5000


def _token():
    r = requests.post(config.ACLED_TOKEN_URL, data={
        'username': config.ACLED_EMAIL,
        'password': config.ACLED_PASSWORD,
        'grant_type': 'password',
        'client_id': 'acled',
        'scope': 'authenticated',
    }, timeout=60)
    r.raise_for_status()
    return r.json()['access_token']


def fetch_acled(start_date='2015-01-01'):
    """Return an event-level DataFrame, or None if credentials are missing."""
    if not (config.ACLED_EMAIL and config.ACLED_PASSWORD):
        print('  ACLED: no ACLED_EMAIL/ACLED_PASSWORD set - skipping fetch')
        return None
    headers = {'Authorization': f'Bearer {_token()}'}
    frames, page = [], 1
    while True:
        r = requests.get(config.ACLED_READ_URL, headers=headers, params={
            '_format': 'csv',
            'country': 'Indonesia',
            'event_date': f'{start_date}|2099-12-31',
            'event_date_where': 'BETWEEN',
            'fields': FIELDS,
            'limit': PAGE_SIZE,
            'page': page,
        }, timeout=300)
        r.raise_for_status()
        chunk = pd.read_csv(io.StringIO(r.text))
        if chunk.empty:
            break
        frames.append(chunk)
        if len(chunk) < PAGE_SIZE:
            break
        page += 1
    if not frames:
        raise RuntimeError('ACLED fetch returned no rows')
    df = pd.concat(frames, ignore_index=True)
    print(f'  ACLED: fetched {len(df):,} events '
          f'({df.event_date.min()} to {df.event_date.max()})')
    return df
