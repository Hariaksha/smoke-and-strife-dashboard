"""Brazil-specific configuration. Kept separate from pipeline/config.py per
the same "fully separate pipeline" architecture used for nigeria/ - Brazil's
code never imports Indonesia-specific paths/BBOX, only the generic building
blocks and shared secrets/endpoint constants.

PANEL_START/FETCH_START are NOT the Dec-2014 start used in
scoping/brazil_round3.py's original (buggy) run. A raw ACLED pull
(fetch_acled_country('Brazil', '2014-12-01')) confirmed ACLED has zero
events for Brazil before 2018-01-01 - a genuine historical-coverage gap
(ACLED expanded into Latin America around then), not a crosswalk artifact.
Starting the panel at 201412/201501 as the other countries do would
zero-fill three years of "no data" as "confirmed no conflict", silently
padding the sample with 44 illegitimate months (~33% of the previously
reported 135-month window). PANEL_START=201801 (FETCH_START one month
earlier, for January 2018's lag) is Brazil's real analogue of Indonesia's/
Nigeria's PANEL_START choice.
"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent  # .../smoke-and-strife-dashboard/brazil
REPO_ROOT = HERE.parent  # .../smoke-and-strife-dashboard

sys.path.insert(0, str(REPO_ROOT))
from pipeline import config as _idn_cfg  # noqa: E402 (secrets/endpoints only)

DATA_DIR = HERE / 'data'
SITE_DATA_DIR = REPO_ROOT / 'site' / 'data'
# Brazil's raw GADM level-2 shapefile is ~264 MB (5,572 municipalities) -
# over GitHub's 100 MB hard file limit, so unlike Nigeria's (~4.4 MB, safely
# committed and re-parsed every weekly run), it is NOT committed to this
# repo. It only needs to exist once, locally, for pipeline_bootstrap.py;
# weekly pipeline_update.py instead loads the small, already-simplified,
# committed districts.parquet artifact (gpd.read_parquet), never touching
# the raw shapefile at all. Bootstrap looks for it at the path below, which
# is where scoping/brazil_round3.py already downloaded and cached it
# (scoping/*_data/ is gitignored as scratch, which is exactly the "local
# only, not committed" property this file needs too).
BOOTSTRAP_SHP = REPO_ROOT / 'scoping' / 'brazil_data' / 'gadm41_BRA_shp' / 'gadm41_BRA_2.shp'

BBOX = (-74.0, -34.0, -34.0, 5.5)  # west, south, east, north
PANEL_START = 201801  # ACLED's real Brazil coverage start (verified via raw pull)
FETCH_START = 201712  # one month earlier, needed to compute Jan 2018's lag
N_LAGS = 1

DISTRICTS_PARQUET = DATA_DIR / 'districts.parquet'
FIRES_PM_PARQUET = DATA_DIR / 'fires_pm.parquet'
INSTRUMENT_PARQUET = DATA_DIR / 'instrument.parquet'
WIND_PM_PARQUET = DATA_DIR / 'wind_pm.parquet'
CONFLICT_PM_PARQUET = DATA_DIR / 'conflict_pm.parquet'
META_JSON = DATA_DIR / 'meta.json'
ROBUSTNESS_JSON = DATA_DIR / 'robustness.json'
RESULTS_JSON = SITE_DATA_DIR / 'results_brazil.json'
DISTRICTS_GEOJSON = SITE_DATA_DIR / 'districts_brazil.geojson'

# ── Secrets/endpoints: same ACLED/FIRMS/CDS accounts as Indonesia/Nigeria,
# reused directly rather than duplicated ───────────────────────────────────
ACLED_EMAIL = _idn_cfg.ACLED_EMAIL
ACLED_PASSWORD = _idn_cfg.ACLED_PASSWORD
ACLED_TOKEN_URL = _idn_cfg.ACLED_TOKEN_URL
ACLED_READ_URL = _idn_cfg.ACLED_READ_URL
FIRMS_MAP_KEY = _idn_cfg.FIRMS_MAP_KEY
FIRMS_AREA_URL = _idn_cfg.FIRMS_AREA_URL
FIRMS_AVAIL_URL = _idn_cfg.FIRMS_AVAIL_URL

# Same ACLED category definitions as the Indonesia/Nigeria pipelines
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
