# Smoke and Strife — Live Dashboard

A weekly-updating dashboard that re-runs the wind-IV model from the paper
["Smoke and Strife: Fire Exposure and Social Conflict in Indonesia"](https://github.com/Hariaksha/wlidfire-conflict)
(upwind fire exposure instrumenting local fire intensity, district-month
panel) on continuously refreshed data.

This is a **standalone repo**, deliberately separate from the paper/analysis
repo (`../climate-conflict`) so the live site has its own history, secrets,
and deploy pipeline. It borrows the paper repo's local archive files once,
at bootstrap time, and never touches it again.

The live site now covers **two countries** via a toggle
(`site/app.js`/`site/index.html`): Indonesia (below) and Nigeria, a second,
independently-validated finding with its own narrative, own fully separate
data pipeline, and own committed artifacts under `nigeria/` — see
[nigeria/README.md](nigeria/README.md) for everything Nigeria-specific.
Everything else in this README describes the original Indonesia pipeline.

| Source | What | Access | Cadence / latency |
|---|---|---|---|
| ACLED | Conflict events | OAuth API (myACLED account) | **Research tier (default): event-level data lags ~12 months, rolling.** Partner/Enterprise: weekly, unlagged. See below. |
| NASA FIRMS | VIIRS S-NPP fire detections | Area API (free MAP_KEY) | NRT within ~3 hours; SP (science-quality) supersedes it ~3 months later |
| ERA5 winds | 10 m u/v components | **Copernicus CDS API (default)**, free, requires a `.cdsapirc`-style key. Earthmover is opt-in only (`ARRAYLAKE_ENABLED=1`) — see below for why it's off by default | CDS: ERA5T monthly means ~5-6 days after month end. Earthmover paid tier: within 4 hours of ECMWF (SLA) if enabled |

> **The binding constraint on "live" is ACLED, not wind or fire.** At the
> free Research myACLED tier, event-level data (the exact lat/lon needed to
> assign an event to one of 447 districts) is only released with a 12-month
> rolling delay. ACLED's *unlagged* weekly aggregated files exist but are
> country-year / country-month-year only — far too coarse for a district
> panel — so they can't substitute. Practically: the panel's end date is
> `min(wind vintage, fire vintage, ACLED vintage)`, and ACLED will always be
> the tightest constraint by about a year unless the account is upgraded.
> See **§2** below for what an upgrade requires.

## Layout

```
smoke-and-strife-dashboard/
  pipeline/        Python package: fetchers, instrument, panel, estimation (Indonesia)
  data/            committed parquet artifacts (district-month aggregates, Indonesia)
  nigeria/         second country: own pipeline, own data/, own README.md
  site/            static dashboard (GitHub Pages); site/data/results.json (Indonesia)
                   and site/data/results_nigeria.json (Nigeria), one static site, country toggle
```

Only district-month aggregates and simplified district polygons are
committed — no raw per-detection or per-event data.

Nigeria reuses only the generic building blocks below (`pipeline/spatial.py`,
`pipeline/instrument.py`, `pipeline/estimate.py`, `pipeline/fetch_wind.py`'s
CDS backend) via a `sys.path` import — it has its own config, fetchers, and
committed artifacts, so nothing in this README's Indonesia-specific setup
needs to change for Nigeria to keep working, and vice versa.

---

## How to get each data source flowing automatically

### 1. ERA5 wind — Copernicus CDS (default)

1. Register/log in at [cds.climate.copernicus.eu](https://cds.climate.copernicus.eu).
2. Go to your **profile page** → **API key** section. It shows a ready-made
   `.cdsapirc` snippet (`url: https://cds.climate.copernicus.eu/api`,
   `key: ...`) — copy the key value.
3. Set `CDSAPI_KEY` as an env var / GitHub secret. Free, no payment or
   subscription anywhere in this flow (CC-BY licence).
4. **If that key is ever exposed** (e.g. committed to git by accident),
   rotate it from the same profile page — the refresh/rotate icon next to
   the key field invalidates the old value and issues a new one instantly.

That's it — `pipeline/fetch_wind.py`'s CDS backend requests the exact
`reanalysis-era5-single-levels-monthly-means` dataset/variables shown on
CDS's own "Show API request code" panel for this dataset, restricted to
the Indonesia bounding box.

### Earthmover (optional, off by default)

Earthmover's Arraylake marketplace offers the same ERA5 data with lower
latency, but it's **disabled unless you set `ARRAYLAKE_ENABLED=1`**. In
practice the free public tier (`earthmover-public/era5`) showed two
different failure modes across otherwise-identical calls during testing —
a clean fast "not logged in" rejection, and an internal retry loop deep in
the `arraylake`/`icechunk` stack that took hours to give up and never
raised an exception back to Python, so the CDS fallback never got a chance
to run. `fetch_wind.py` now wraps every fetch attempt in a hard wall-clock
timeout (`_with_timeout`, 90s) regardless of backend, but since the
underlying S3 access is Rust code (icechunk, via an async runtime), a
thread `join(timeout)` may not reliably interrupt every failure mode.
Re-enable only if you've verified it against a real reproduction of that
retry-loop failure, or if you're using the paid low-latency tier instead
(set `ARRAYLAKE_REPO` to `{your_org}/era5` and `ARRAYLAKE_TOKEN`, from
`app.earthmover.io/marketplace/6a18ae1ba1c8feafd01f2b76` — a paid
subscription, confirm pricing yourself before subscribing).

### 2. ACLED conflict events

**No separate API key exists.** ACLED retired key-based access; myACLED
(your account's email + password) *is* the credential, exchanged for a
short-lived OAuth token on every call. There is nothing to "generate" beyond
registering the account itself.

1. Register/confirm a **myACLED** account at
   [acleddata.com](https://acleddata.com/user/register). I checked your
   logged-in session (confirmed 2026-07-16): you're on the **Research**
   tier.
2. Programmatic access:
   - `POST https://acleddata.com/oauth/token` with form fields
     `grant_type=password`, `client_id=acled`, `scope=authenticated`, your
     `username` (email) and `password` → a bearer access token (24 h) and a
     refresh token (14 days).
   - That token authorizes `GET https://acleddata.com/api/acled/read`,
     which is what `pipeline/fetch_acled.py` calls, filtered to
     `country=Indonesia`.
3. Set `ACLED_EMAIL` and `ACLED_PASSWORD` as env vars / GitHub secrets.
   The pipeline re-authenticates every run rather than caching the token,
   so nothing expires silently between weekly runs.
4. **The Research-tier catch:** per ACLED's own FAQ
   ([acleddata.com/faq-codebook-tools](https://acleddata.com/faq-codebook-tools),
   "ACLED's Latent Event Data"), event-level (disaggregated) data at the
   Research tier is **"latent data" — available with a 12-month delay,
   updated on a rolling basis.** The API will happily return data for
   Indonesia going back to 2015, but its *most recent* month will always
   sit roughly a year behind wherever "today" is. This is not a pipeline
   bug — it's the access tier. `run_update.py` already handles it
   correctly (it takes the newest month ACLED actually returns and treats
   that as the ACLED vintage), so the dashboard just runs a year behind on
   the conflict side, which shows up honestly in the vintage chips.
5. **To get real-time weekly data** (matching what "myACLED member access
   levels" calls **Partner** — "Disaggregated Event Data (weekly)" — or
   **Enterprise** — "Unlimited Event Data (weekly, expedited)"), you need a
   license upgrade. This isn't self-serve: email
   [access@acleddata.com](mailto:access@acleddata.com) or
   [licensing@acleddata.com](mailto:licensing@acleddata.com) explaining the
   research use case, or use the **"Request further access"** link on the
   [myACLED FAQ page](https://acleddata.com/myacled-faqs). ACLED states
   upgrades typically take about one business day to provision once
   approved. No code changes needed on the upgrade — the same OAuth flow
   and `fetch_acled.py` just start returning current data.
6. Nothing to schedule on ACLED's side beyond that — every pipeline run
   re-pulls the full Indonesia history (~25k events, a few seconds), which
   also absorbs ACLED's own historical revisions.

### 3. NASA FIRMS fire detections

1. Request a free **MAP_KEY** at
   [firms.modaps.eosdis.nasa.gov/api/map_key](https://firms.modaps.eosdis.nasa.gov/api/map_key/)
   (instant, just an email address — no approval wait).
2. Set `FIRMS_MAP_KEY` as an env var / GitHub secret.
3. No further setup — `pipeline/fetch_fires.py` calls the **Area API**
   (`/api/area/csv/{MAP_KEY}/{SOURCE}/{bbox}/{days}/{date}`) against the
   Indonesia bounding box, in ≤5-day windows (`MAX_DAYS` in that file —
   verified empirically that both `VIIRS_SNPP_SP` and `VIIRS_SNPP_NRT`
   reject `days=10` with "Invalid day range. Expects [1..5]." on this
   MAP_KEY, contradicting FIRMS's general documented max of 10; may be a
   per-key/tier limit rather than a global constant):
   - `VIIRS_SNPP_NRT` for anything not yet in the science-quality archive
     (flagged *preliminary* on the dashboard),
   - `VIIRS_SNPP_SP` once it becomes available for a given month (checked
     via the `data_availability` endpoint each run), which supersedes the
     NRT detections and clears the preliminary flag.
4. Rate limit is 5,000 transactions per 10 minutes — a weekly Indonesia
   pull uses a few dozen, nowhere close.

None of these three sources need a cron job of their own — the single
GitHub Actions schedule below drives all three fetches.

---

## One-time setup

1. **Bootstrap the artifacts** from the paper repo's local archive data
   (only needs to happen once; nothing here is committed to git history
   afterward beyond the resulting parquet files):

   ```bash
   python -m pipeline.bootstrap
   ```

   This reads `../climate-conflict/data/...` (GADM shapefile, ERA5
   archive, VIIRS archive CSV, ACLED xlsx) — adjust
   `pipeline/config.py:PAPER_REPO` if that repo lives somewhere else.

2. **Create the four credentials** described above: myACLED account, FIRMS
   MAP_KEY, and a CDS API key (Earthmover is optional — see above).

3. **Add GitHub Actions secrets** — repo → **Settings → Secrets and
   variables → Actions → Secrets tab** (not the *Variables* tab next to
   it, which is for non-sensitive plain-text config and is a different
   thing): `ACLED_EMAIL`, `ACLED_PASSWORD`, `FIRMS_MAP_KEY`, `CDSAPI_KEY`.
   Add `ARRAYLAKE_ENABLED`, `ARRAYLAKE_REPO`, `ARRAYLAKE_TOKEN` too only if
   opting into Earthmover. These live entirely in GitHub's own encrypted
   vault — nothing about them touches git history or `.gitignore`, so
   there's no file to push; you enter each value directly into that form,
   and updating one later means overwriting it there, not editing a file.

4. **Enable GitHub Pages**: repo → Settings → Pages → Source: *GitHub
   Actions*.

5. Trigger the workflow once by hand (Actions → *Update live dashboard* →
   *Run workflow*). It then runs every Wednesday 06:00 UTC.

## Local run

Copy `secrets.env.example` to `secrets.env` (gitignored — real credentials
never get committed) and fill in your values, then:

```bash
pip install -r requirements.txt
source secrets.env
python -m pipeline.run_update       # Indonesia: fetch increments + re-estimate
python -m nigeria.pipeline_update   # Nigeria: same, fully separate artifacts
python -m http.server -d site 8000 # view at http://localhost:8000 (both toggles)
```

Missing credentials are skipped gracefully — the model is simply
re-estimated on the stored data vintages. Each of the three fetch steps in
`run_update.py` (and, identically, `nigeria/pipeline_update.py`) is
independently wrapped in a `try/except`, so one source's failure (a
transient timeout, an API hiccup) can't block the other two or stop that
country's `results.json` from being rewritten with whatever vintages are
actually available — and a failure in one country's update can't affect
the other's at all, since they're entirely separate scripts and artifacts.

## How the weekly update works

1. **ACLED**: the full Indonesia window is re-fetched every run, which also
   absorbs ACLED's historical revisions.
2. **Wind**: any months after the stored vintage are fetched via CDS (or
   Earthmover, only if `ARRAYLAKE_ENABLED=1`) and aggregated to district
   level.
3. **Fires**: every month after the last *science-quality* (SP) month is
   re-fetched (SP where newly available, NRT otherwise); district-month FRP
   and the upwind instrument are recomputed for exactly those months.
4. The balanced panel is rebuilt through the latest month all three sources
   cover, the IV specifications are re-estimated (first stage, full-panel
   IV, conflict-active thresholds, event-type decompositions, expanding-
   window series), and `site/data/results.json` is rewritten.
5. CI commits the refreshed artifacts and deploys `site/` to GitHub Pages.

CI runs Nigeria's update (`python -m nigeria.pipeline_update`) as a second,
independent step right after Indonesia's, with `continue-on-error: true` —
a Nigeria-side failure can never block Indonesia's commit or deploy. Both
countries' refreshed artifacts (`data/`, `nigeria/data/`, `site/data/`) are
committed together and deployed as one static site.

Months whose fire data is still NRT-quality are marked *preliminary* in the
dashboard; estimates including them get re-run automatically as SP data
supersedes NRT in later weeks.

## Plain-language translation

Alongside the raw coefficients/p-values, the conflict-active threshold
section renders a plain-English summary (e.g. "~8% fewer conflict events")
for readers without a statistics background — computed live in
`site/app.js` (`plainLanguageHTML`) from whatever `results.json` currently
holds, not hardcoded to one week's numbers. It converts a coefficient into
a percentage of that subsample's mean outcome (`mean_events`/
`mean_pv_events`, added to `conflict_active_table` in `pipeline/
estimate.py` specifically for this), and only states a directional claim
when p < 0.10 — otherwise it explicitly says no significant effect was
detected, rather than presenting a noisy point estimate as a finding.
