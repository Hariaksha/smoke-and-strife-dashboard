"""Brazil FIRMS fetch, mirroring nigeria/fires.py exactly (same SP/NRT
split, same preliminary-flagging logic) but with Brazil's bounding box.
MAX_DAYS=5, computed empirically for the earlier scoping runs against
Brazil's much higher fire-detection density (5,572 municipalities).
"""
import io
import time
from datetime import date, timedelta

import pandas as pd
import requests

from . import config as cfg

AREA = ','.join(str(v) for v in cfg.BBOX)
MAX_DAYS = 5


def sp_last_available():
    url = f'{cfg.FIRMS_AVAIL_URL}/{cfg.FIRMS_MAP_KEY}/VIIRS_SNPP_SP'
    r = requests.get(url, timeout=60)
    r.raise_for_status()
    df = pd.read_csv(io.StringIO(r.text))
    return pd.to_datetime(df['max_date'].iloc[0]).date()


def _fetch_range(source, start, end):
    frames = []
    cur = start
    while cur <= end:
        days = min(MAX_DAYS, (end - cur).days + 1)
        url = (f'{cfg.FIRMS_AREA_URL}/{cfg.FIRMS_MAP_KEY}/{source}/'
               f'{AREA}/{days}/{cur.isoformat()}')
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
        time.sleep(0.3)
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


def _month_before(d):
    first_of_month = d.replace(day=1)
    prev_end = first_of_month - timedelta(days=1)
    return prev_end.year * 100 + prev_end.month


def fetch_fires_since(start_ym):
    """Fetch all detections from the first day of start_ym through today.

    Returns (detections, new_final_through_ym), or (None, None) if no key.
    """
    if not cfg.FIRMS_MAP_KEY:
        print('  FIRMS: no FIRMS_MAP_KEY set - skipping fetch')
        return None, None

    start_y, start_m = divmod(start_ym, 100)
    start = date(start_y, start_m, 1)
    today = date.today()

    sp_max = sp_last_available()
    print(f'  FIRMS: SP available through {sp_max}')

    parts = []
    if sp_max >= start:
        sp = _fetch_range('VIIRS_SNPP_SP', start, min(sp_max, today))
        if len(sp):
            sp['is_nrt'] = False
            parts.append(sp)
            print(f'  FIRMS: {len(sp):,} SP detections')
    nrt_start = max(start, sp_max + timedelta(days=1))
    if nrt_start <= today:
        nrt = _fetch_range('VIIRS_SNPP_NRT', nrt_start, today)
        if len(nrt):
            nrt['is_nrt'] = True
            parts.append(nrt)
            print(f'  FIRMS: {len(nrt):,} NRT detections')

    if not parts:
        return pd.DataFrame(), _month_before(sp_max)

    df = pd.concat(parts, ignore_index=True)
    if 'type' in df.columns:
        df = df[df['type'].fillna(0).astype(int).eq(0) | df['is_nrt']]
    df = df[df['confidence'].astype(str).isin(['h', 'n'])]
    df['acq_date'] = pd.to_datetime(df['acq_date'])
    df['year'] = df['acq_date'].dt.year
    df['month'] = df['acq_date'].dt.month
    df = df[['latitude', 'longitude', 'frp', 'year', 'month', 'is_nrt']]
    print(f'  FIRMS: {len(df):,} detections after filters')
    return df, _month_before(sp_max)
