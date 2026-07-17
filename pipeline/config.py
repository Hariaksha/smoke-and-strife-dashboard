"""Central configuration for the live-dashboard pipeline.

All secrets come from environment variables so nothing sensitive is ever
committed:
    ACLED_EMAIL / ACLED_PASSWORD  - myACLED account (OAuth password grant)
    FIRMS_MAP_KEY                 - NASA FIRMS map key
    ARRAYLAKE_REPO / ARRAYLAKE_TOKEN
                                   - Earthmover Arraylake. Defaults to the
                                     free public "earthmover-public/era5"
                                     repo (quarterly updates, no token
                                     needed). Set ARRAYLAKE_REPO to
                                     "{your_org}/era5" and ARRAYLAKE_TOKEN
                                     to use the paid "ERA5 (Daily Updates)"
                                     marketplace subscription instead.
    CDSAPI_URL / CDSAPI_KEY       - Copernicus CDS (ERA5), fallback wind
                                     backend if Earthmover isn't configured
"""
import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = REPO_ROOT / 'data'          # committed pipeline artifacts (parquet)
SITE_DIR = REPO_ROOT / 'site'
SITE_DATA_DIR = SITE_DIR / 'data'     # JSON consumed by the dashboard

# Sibling repo holding the paper and the one-time bootstrap archive data.
# Only used by pipeline/bootstrap.py, which runs once locally - never in CI.
PAPER_REPO = REPO_ROOT.parent / 'climate-conflict'

# ── Analysis parameters (identical to analysis/wind-IV.ipynb) ─────────────
UPWIND_RADIUS_KM = 300
UPWIND_HALF_ANG = 45
START_YEAR = 2015
N_LAGS = 1
R_EARTH = 6371.0
ACTIVE_THRESHOLDS = [0.05, 0.10, 0.20, 0.30]

POLITICAL_VIOLENCE = [
    'Riots', 'Violence against civilians',
    'Battles', 'Explosions/Remote violence',
]
RIOTS_PROTESTS = ['Riots', 'Violence against civilians']
BATTLES_VIOLENCE = ['Battles', 'Explosions/Remote violence']
FOURWAY_TYPES = {
    'protests': 'Protests',
    'riots': 'Riots',
    'violence_against_civilians': 'Violence against civilians',
    'strategic_developments': 'Strategic developments',
}

# Indonesia bounding box (west, south, east, north) - matches the ERA5 pull
BBOX = (95.0, -11.0, 141.0, 6.0)

# ── Data source endpoints ─────────────────────────────────────────────────
ACLED_TOKEN_URL = 'https://acleddata.com/oauth/token'
ACLED_READ_URL = 'https://acleddata.com/api/acled/read'
FIRMS_AREA_URL = 'https://firms.modaps.eosdis.nasa.gov/api/area/csv'
FIRMS_AVAIL_URL = 'https://firms.modaps.eosdis.nasa.gov/api/data_availability/csv'
CDS_DATASET = 'reanalysis-era5-single-levels-monthly-means'

# ── Secrets (never hardcode) ──────────────────────────────────────────────
ACLED_EMAIL = os.environ.get('ACLED_EMAIL', '')
ACLED_PASSWORD = os.environ.get('ACLED_PASSWORD', '')
FIRMS_MAP_KEY = os.environ.get('FIRMS_MAP_KEY', '')
CDSAPI_URL = os.environ.get('CDSAPI_URL', 'https://cds.climate.copernicus.eu/api')
CDSAPI_KEY = os.environ.get('CDSAPI_KEY', '')
ARRAYLAKE_TOKEN = os.environ.get('ARRAYLAKE_TOKEN', '')  # only for the paid tier
ARRAYLAKE_REPO = os.environ.get('ARRAYLAKE_REPO', 'earthmover-public/era5')

# ── Bootstrap-only inputs (local archive files in the sibling paper repo,
# not needed in CI - see pipeline/bootstrap.py) ───────────────────────────
BOOT_WIND_NC = PAPER_REPO / 'data/climate/wind/era5_wind_indonesia_2015_2025.nc'
BOOT_FIRE_CSV = PAPER_REPO / ('data/climate/fire/DL_FIRE_SUOMI-VIIRS-C2_718931_'
                              'Nov2000-Feb2026_buffer0km-csv/fire_archive_SV-C2_718931.csv')
BOOT_ACLED_XLSX = PAPER_REPO / 'data/unrest/ACLED Data_2026-06-10.xlsx'
BOOT_DISTRICT_SHP = PAPER_REPO / 'data/administrative/gadm41_IDN_shp/gadm41_IDN_2.shp'

# ── Artifact paths ────────────────────────────────────────────────────────
DISTRICTS_PARQUET = DATA_DIR / 'districts.parquet'
FIRES_PM_PARQUET = DATA_DIR / 'fires_pm.parquet'
INSTRUMENT_PARQUET = DATA_DIR / 'instrument.parquet'
WIND_PM_PARQUET = DATA_DIR / 'wind_pm.parquet'
CONFLICT_PM_PARQUET = DATA_DIR / 'conflict_pm.parquet'
META_JSON = DATA_DIR / 'meta.json'
RESULTS_JSON = SITE_DATA_DIR / 'results.json'
DISTRICTS_GEOJSON = SITE_DATA_DIR / 'districts.geojson'


def ym(year, month):
    return year * 100 + month


def ym_iter(start_ym, end_ym):
    """Yield (year, month) tuples from start_ym to end_ym inclusive."""
    y, m = divmod(start_ym, 100)
    while ym(y, m) <= end_ym:
        yield y, m
        m += 1
        if m > 12:
            y, m = y + 1, 1
