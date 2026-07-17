# Smoke and Strife — Live Dashboard

A weekly-updating dashboard that re-runs the wind-IV model from the paper
["Smoke and Strife: Fire Exposure and Social Conflict in Indonesia"](https://github.com/Hariaksha/wlidfire-conflict)
(upwind fire exposure instrumenting local fire intensity, district-month
panel) on continuously refreshed data.

This is a **standalone repo**, deliberately separate from the paper/analysis
repo (`../climate-conflict`) so the live site has its own history, secrets,
and deploy pipeline. It borrows the paper repo's local archive files once,
at bootstrap time, and never touches it again.

| Source | What | Access | Cadence / latency |
|---|---|---|---|
| ACLED | Conflict events | OAuth API (myACLED account) | **Research tier (default): event-level data lags ~12 months, rolling.** Partner/Enterprise: weekly, unlagged. See below. |
| NASA FIRMS | VIIRS S-NPP fire detections | Area API (free MAP_KEY) | NRT within ~3 hours; SP (science-quality) supersedes it ~3 months later |
| ERA5 winds | 10 m u/v components | **Earthmover "ERA5 (Daily Updates)"** marketplace subscription (primary) or Copernicus CDS API (fallback) | Earthmover: within 4 hours of ECMWF (SLA), ERA5T→final over ~2-3 months. CDS: ERA5T monthly means ~5-6 days after month end |

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
  pipeline/        Python package: fetchers, instrument, panel, estimation
  data/            committed parquet artifacts (district-month aggregates)
  site/            static dashboard (GitHub Pages); site/data/results.json
```

Only district-month aggregates and simplified district polygons are
committed — no raw per-detection or per-event data.

---

## How to get each data source flowing automatically

### 1. ERA5 wind — Earthmover Marketplace (what you found)

The listing at `app.earthmover.io/marketplace/6a18ae1ba1c8feafd01f2b76` is
**"ERA5 (Daily Updates)"**, the paid low-latency edition of Earthmover's
cloud-optimized ERA5 (the free `earthmover-public/era5` product only
refreshes quarterly — too slow for a weekly dashboard). Steps:

1. Log in at [app.earthmover.io](https://app.earthmover.io) and open that
   marketplace listing → **"Log In to Subscribe"**. This is a paid product;
   confirm pricing/terms in the marketplace UI before subscribing (I can't
   see or accept pricing on your behalf).
2. Once subscribed, the dataset lives at `{your_org}/era5` under your
   Arraylake org. Find your org slug in the Arraylake console.
3. Generate an API token: Arraylake console → your profile/org settings →
   **API tokens** → create one scoped to read access.
4. `pip install arraylake icechunk` (already in `requirements.txt`).
5. Set `ARRAYLAKE_TOKEN` (the token) and `ARRAYLAKE_ORG` (your org slug) as
   environment variables locally, and as GitHub Actions secrets for CI.

That's it — `pipeline/fetch_wind.py` reads `single/temporal` (hourly
u10/v10, tiled for efficient regional time-series reads), checks each
hour's QC `status` flag before averaging (so a not-yet-ingested or
still-processing hour never silently zero-fills a monthly mean), and
restricts to the Indonesia bounding box. If `ARRAYLAKE_TOKEN`/`ARRAYLAKE_ORG`
aren't set, it falls back to the free CDS API automatically — no code
changes needed either way.

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
   Indonesia bounding box, in ≤10-day windows (an API limit, not a choice):
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
   MAP_KEY, an Earthmover Arraylake token + org (or skip and rely on the
   free CDS fallback — register at cds.climate.copernicus.eu and create an
   API token there instead).

3. **Add GitHub Actions secrets** (repo → Settings → Secrets → Actions):
   `ACLED_EMAIL`, `ACLED_PASSWORD`, `FIRMS_MAP_KEY`, and either
   (`ARRAYLAKE_TOKEN` + `ARRAYLAKE_ORG`) or `CDSAPI_KEY`.

4. **Enable GitHub Pages**: repo → Settings → Pages → Source: *GitHub
   Actions*.

5. Trigger the workflow once by hand (Actions → *Update live dashboard* →
   *Run workflow*). It then runs every Wednesday 06:00 UTC.

## Local run

```bash
pip install -r requirements.txt
export ACLED_EMAIL=... ACLED_PASSWORD=... FIRMS_MAP_KEY=...
export ARRAYLAKE_TOKEN=... ARRAYLAKE_ORG=...   # or CDSAPI_KEY=...
python -m pipeline.run_update      # fetch increments + re-estimate
python -m http.server -d site 8000 # view at http://localhost:8000
```

Missing credentials are skipped gracefully — the model is simply
re-estimated on the stored data vintages.

## How the weekly update works

1. **ACLED**: the full Indonesia window is re-fetched every run, which also
   absorbs ACLED's historical revisions.
2. **Wind**: any months after the stored vintage are fetched (Earthmover if
   configured, else CDS) and aggregated to district level.
3. **Fires**: every month after the last *science-quality* (SP) month is
   re-fetched (SP where newly available, NRT otherwise); district-month FRP
   and the upwind instrument are recomputed for exactly those months.
4. The balanced panel is rebuilt through the latest month all three sources
   cover, the IV specifications are re-estimated (first stage, full-panel
   IV, conflict-active thresholds, event-type decompositions, expanding-
   window series), and `site/data/results.json` is rewritten.
5. CI commits the refreshed artifacts and deploys `site/` to GitHub Pages.

Months whose fire data is still NRT-quality are marked *preliminary* in the
dashboard; estimates including them get re-run automatically as SP data
supersedes NRT in later weeks.
