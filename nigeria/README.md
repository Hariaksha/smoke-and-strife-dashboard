# Nigeria

The dashboard's second country. Started as a scoping exercise for whether
the wind-IV design from
["Smoke and Strife: Fire Exposure and Social Conflict in Indonesia"](https://github.com/Hariaksha/wlidfire-conflict)
(upwind fire exposure instrumenting local fire intensity, district-month
panel) extends to Nigeria — one of two candidates identified from the
paper's own "Future Research Directions" section (the other being Brazil).
It found a genuine, if differently-shaped, result and is now live: a
weekly-updating second tab on the same public dashboard
([site/index.html](../site/index.html), country toggle), refreshed by the
same GitHub Actions schedule as Indonesia.

Lives inside `smoke-and-strife-dashboard` and reuses its generic pipeline
building blocks (`pipeline/spatial.py`, `pipeline/instrument.py`,
`pipeline/estimate.py`, `pipeline/fetch_wind.py`'s CDS backend) via a
`sys.path` import, but has its **own fully separate data pipeline** —
own config, own fetchers, own committed parquet artifacts — so a bug or
outage on the Nigeria side can never affect Indonesia's production data
(see `pipeline_update.py`'s per-source try/except isolation, mirroring
Indonesia's `pipeline/run_update.py`).

## Why Nigeria

- Real, satellite-confirmed seasonal fire activity (Harmattan-season
  agricultural/bush burning), with a highly consistent seasonal wind regime
  that turned out to make the upwind instrument even *stronger* than
  Indonesia's (see Results below).
- GADM admin-2 granularity (775 LGAs) is a close match to Indonesia's 447
  districts — unlike Brazil's ~3,750 municipalities, which would need a
  much bigger computational lift.
- ACLED's actual event-type mix (35.8% violence against civilians, 26.6%
  battles, 19.3% protests, 8.5% riots — pulled directly from the API, not
  estimated) has far more of the categories the paper's mechanism was
  actually found to operate through (Riots + Violence against civilians)
  than Indonesia's protest-dominated sample (80.7% protests).

## Layout

```
smoke-and-strife-dashboard/
  nigeria/
    README.md
    config.py               paths, BBOX, panel window, ym_iter() helpers
    acled.py                Nigeria ACLED fetch + admin2->GADM LGA crosswalk + event-type tagging
    fires.py                Nigeria FIRMS fetch (SP/NRT, MAX_DAYS=5), standalone (not import-shared,
                             since pipeline/fetch_fires.py's AREA is a frozen module constant)
    wind.py                 year-batched CDS wind fetch, shared by bootstrap + weekly update
    pipeline_bootstrap.py   one-time: derives committed artifacts from analysis/nigeria_panel.parquet
    pipeline_update.py      weekly: incremental ACLED/wind/fire refresh -> site/data/results_nigeria.json
    data/*.parquet          committed district-month artifacts (districts, conflict, fires, wind, instrument)
    data/meta.json          per-source vintage tracking (wind/fire-final/fire-prelim/acled "through" months)
    data/robustness.json    placebo-test + Conley-SE results, point-in-time, refreshed manually (see below)
    data/administrative/gadm41_NGA_shp/  GADM level 0-2 boundaries (level 2 = 775 LGAs)
    analysis/build_panel.py         original one-off panel build + estimation (see "History" below)
    analysis/nigeria_panel.parquet  its output — bootstrap's source of truth, reconstructed from exactly once
    analysis/event_types.py         event-type decomposition (produced the Riots/Strategic-developments finding)
    analysis/placebo_test.py        placebo-instrument robustness check -> data/robustness.json
    analysis/conley_se.py           Conley spatial-correlation SE robustness check -> data/robustness.json
```

`pipeline_bootstrap.py` and `pipeline_update.py` are the production path
(what the weekly GitHub Action actually runs). `analysis/*.py` are the
original research scripts that produced the findings in the first place —
kept because they're not reproducible from the production pipeline alone
(e.g. the instrument-construction step there takes 3+ hours; production
artifacts were derived from their *output*, not by re-running them — see
"History" below) and because `placebo_test.py`/`conley_se.py` are
deliberately **not** part of the weekly re-estimation (see next section).

## Results so far

**Round 1 — crude gut check** (1-degree analysis grid, no real
boundaries, 3 months of data, fire+wind only, no conflict side): first-stage
F ≈ 70. Established that the physical upwind-fire-transport relationship
holds in Nigeria at all.

**Round 2 — real boundaries, 24-month window** (775 real LGAs, Aug 2023–Jul
2025): first-stage F = 1,131.5. Conflict-active heterogeneity showed the
same *shape* as the paper's Indonesia result (coefficients monotonically
more negative as the threshold τ rises) but nothing significant (all
p > 0.3) — plausibly underpowered at only 24 months per district vs. the
paper's 125.

**Round 3 — full 2015-01–2025-07 window** (matching the paper's own span;
775 LGAs, 88,138 district-months, 127 months): the power hypothesis from
round 2 did **not** pan out the way expected.
- **First-stage F = 6,276.5** (coef 0.318, p<0.0001) — stronger than the
  paper's own Indonesia first-stage (F ≈ 4,447). The instrument is
  unambiguously valid in Nigeria.
- Full-panel average effect: null, consistent with the paper (all p > 0.17).
- Conflict-active heterogeneity: the same directional shape persists
  (increasingly negative with higher τ), but coefficients roughly **halved
  in magnitude** versus round 2 rather than holding steady with tighter
  errors — τ≥30% events went from −0.070 (p=0.32) to −0.039 (p=0.25).
  More data made the estimate *smaller*, not just more precise. That's
  more consistent with round 2 being a noisy overestimate than with a
  real effect that just needed more power to detect.

**Round 4 — event-type decomposition** (`analysis/event_types.py`, τ≥30%
conflict-active subsample, same subsample as the aggregate cut above): the
"Not yet tried" hypothesis in an earlier draft of this README guessed the
signal would land in **Riots and Violence against civilians**, mirroring
Indonesia's own finding. That guess was half right. What actually showed
up:
- **Riots: coef ≈ −0.010 to −0.018, p < 0.05** (varies slightly by data
  vintage — the dashboard re-estimates this weekly).
- **Strategic developments: coef ≈ −0.013, p < 0.001** — the stronger and
  more stable of the two. ("Strategic developments" is ACLED's category for
  leadership/coup-related activity, not one Indonesia's own paper features
  as a headline category.)
- Protests, Battles/Violence, and **Violence against civilians itself**
  show no significant response in Nigeria — the opposite of Indonesia,
  where VAC was one of the two headline categories.

**Round 5 — robustness checks** (`analysis/placebo_test.py`,
`analysis/conley_se.py`, results frozen in `data/robustness.json`, checked
2026-07-17):
- **Placebo test** (instrument swapped for a ≥500km-distant district's,
  which has no plausible physical wind-transport channel): Riots and
  Strategic developments both pass — their effect disappears/flips sign
  under the placebo instrument, as it should if the real effect is
  wind-transport-driven. Protests does **not** pass as cleanly.
- **Conley spatial-correlation SEs** (allowing residual correlation across
  province boundaries, three bandwidths: 100/200/500km): Riots and
  Strategic developments both remain significant, some p-values even
  tighter than under province clustering. Violence against civilians'
  *null* also survives Conley SEs (p stays ≈0.96-0.97 throughout) —
  confirming its non-effect is a real null, not a clustering artifact.

**Bottom line**: 2 of 2 robustness checks support the two headline
categories (Riots, Strategic developments). This is a real, differently-
shaped result from Indonesia's own — an aggregate null with two specific
event-type exceptions, versus Indonesia's aggregate finding — which is why
the dashboard gives it its own narrative rather than reusing Indonesia's
template (see `site/app.js`'s `renderNigeriaTiles`/`renderRobustNote`).

## History: from scoping script to production pipeline

The historical build above (`analysis/build_panel.py`, run 2026-07-17) is
the only time raw FIRMS/ACLED/CDS data was fetched for the full 2015-2025
window — see "Historical runtime" below for why it's too expensive to
re-run. `pipeline_bootstrap.py` derived the production `data/*.parquet` artifacts
from its saved output (`analysis/nigeria_panel.parquet`) via an exact
`log1p`/`expm1` inversion of the stored `log_frp`/`log_upwind_frp` columns
— skipping both the ~20min FIRMS refetch and the 3+ hour instrument
rebuild, since those values round-trip losslessly. Only ERA5 wind
(`wind_speed`/`wind_dir_from`) had to be fetched fresh, since it was never
persisted as its own column. From that point on, `pipeline_update.py` is
what actually runs weekly — it never touches `analysis/` again.

**Historical runtime** (`build_panel.py`, full 2015-2025 window,
2026-07-17): ~3.5 hours, not the 30-45 minutes originally estimated. FIRMS
(~20 min) and the year-batched CDS wind fetch (~6 min) matched their
estimates closely; instrument construction was the surprise, taking
**over 3 hours alone**. Cause: Nigeria has 5.8M fire detections vs.
Indonesia's ~2.1M over a comparable span, and KD-tree radius-query cost
scales worse than linearly with point density — every one of the ~99,200
district-months' queries has substantially more points to filter within
its 300km radius. This cost is now paid exactly once (bootstrap); the
weekly `pipeline_update.py` only ever recomputes the instrument for
new/changed months, so it stays fast indefinitely.

## Current status

Live on the dashboard, re-estimated weekly (First-stage F ≈ 6,275 as of
the last run — stronger than Indonesia's own ≈4,475). The event-type
finding (Riots, Strategic developments) has passed both robustness checks
run against it so far. That validation is real but **less extensive**
than what Indonesia's own paper underwent (which is why the dashboard
surfaces this caveat directly rather than presenting the two findings as
equally vetted) — the placebo test and Conley SEs above are a point-in-time
snapshot (`data/robustness.json`), not part of the automatic weekly
re-estimation, and would need to be re-run manually
(`analysis/placebo_test.py`, `analysis/conley_se.py`) to refresh.
