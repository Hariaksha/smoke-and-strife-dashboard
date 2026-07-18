"""Nigeria-specific configuration. Kept separate from pipeline/config.py per
the "fully separate pipeline" architecture decision (see the plan this was
built from) - Nigeria's code never imports Indonesia-specific paths/BBOX,
only the generic building blocks and shared secrets/endpoint constants,
which are the same ACLED/FIRMS/CDS credentials either way.
"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent  # .../smoke-and-strife-dashboard/nigeria
REPO_ROOT = HERE.parent  # .../smoke-and-strife-dashboard

sys.path.insert(0, str(REPO_ROOT))
from pipeline import config as _idn_cfg  # noqa: E402 (secrets/endpoints only)

DATA_DIR = HERE / 'data'
SITE_DATA_DIR = REPO_ROOT / 'site' / 'data'
SHP = DATA_DIR / 'administrative/gadm41_NGA_shp/gadm41_NGA_2.shp'
PANEL_PARQUET = HERE / 'analysis' / 'nigeria_panel.parquet'

BBOX = (2.5, 4.0, 14.8, 14.0)  # west, south, east, north
PANEL_START = 201501  # the panel itself starts here
FETCH_START = 201412  # one month earlier, needed to compute Jan 2015's lag
N_LAGS = 1

DISTRICTS_PARQUET = DATA_DIR / 'districts.parquet'
FIRES_PM_PARQUET = DATA_DIR / 'fires_pm.parquet'
INSTRUMENT_PARQUET = DATA_DIR / 'instrument.parquet'
WIND_PM_PARQUET = DATA_DIR / 'wind_pm.parquet'
CONFLICT_PM_PARQUET = DATA_DIR / 'conflict_pm.parquet'
META_JSON = DATA_DIR / 'meta.json'
ROBUSTNESS_JSON = DATA_DIR / 'robustness.json'  # point-in-time; see analysis/placebo_test.py, conley_se.py
RESULTS_JSON = SITE_DATA_DIR / 'results_nigeria.json'
DISTRICTS_GEOJSON = SITE_DATA_DIR / 'districts_nigeria.geojson'

# ── Secrets/endpoints: same ACLED/FIRMS/CDS accounts as Indonesia, reused
# directly rather than duplicated ──────────────────────────────────────────
ACLED_EMAIL = _idn_cfg.ACLED_EMAIL
ACLED_PASSWORD = _idn_cfg.ACLED_PASSWORD
ACLED_TOKEN_URL = _idn_cfg.ACLED_TOKEN_URL
ACLED_READ_URL = _idn_cfg.ACLED_READ_URL
FIRMS_MAP_KEY = _idn_cfg.FIRMS_MAP_KEY
FIRMS_AREA_URL = _idn_cfg.FIRMS_AREA_URL
FIRMS_AVAIL_URL = _idn_cfg.FIRMS_AVAIL_URL

# Same ACLED category definitions as the Indonesia pipeline
POLITICAL_VIOLENCE = _idn_cfg.POLITICAL_VIOLENCE
RIOTS_PROTESTS = _idn_cfg.RIOTS_PROTESTS
BATTLES_VIOLENCE = _idn_cfg.BATTLES_VIOLENCE
FOURWAY_TYPES = _idn_cfg.FOURWAY_TYPES


def ym(y, m):
    return y * 100 + m


def ym_iter(start_ym, end_ym):
    y, m = divmod(start_ym, 100)
    while ym(y, m) <= end_ym:
        yield y, m
        m += 1
        if m > 12:
            y, m = y + 1, 1
