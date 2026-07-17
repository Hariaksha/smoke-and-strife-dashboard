"""District boundaries, spatial joins, and the ACLED->GADM name crosswalk.

Ported from analysis/wind-IV.ipynb cells 1b, 3, 4 and 5.
"""
import re

import geopandas as gpd
import numpy as np
import pandas as pd

from . import config

# ── ACLED admin2 (English) -> GADM district (Indonesian) crosswalk ────────
DIR_MAP = {
    'North': 'Utara', 'South': 'Selatan', 'East': 'Timur', 'West': 'Barat',
    'Central': 'Tengah', 'Southeast': 'Tenggara', 'Southwest': 'Barat Daya',
}

ADMIN2_OVERRIDES = {
    'Yogyakarta': 'Kota Yogyakarta',
    'Medan': 'Kota Medan',
    'Bukit Tinggi': 'Bukittinggi',
    'Pematang Siantar': 'Pematangsiantar',
    'Tanjung Pinang': 'Tanjungpinang',
    'Padang Sidempuan': 'Padangsidimpuan',
    'Sawah Lunto': 'Sawahlunto',
    'Sawahlunto Sijunjung': 'Sijunjung',
    'Tebing Tinggi': 'Tebingtinggi',
    'Pangkajene and Islands': 'Pangkajene Dan Kepulauan',
    'East Tanjung Jabung': 'Tanjung Jabung T',
    'West Tanjung Jabung': 'Tanjung Jabung B',
    'South Central Timor': 'Timor Tengah Selatan',
    'North Central Timor': 'Timor Tengah Utara',
    'East Kolaka': 'Kolaka',
    'North Labuhan Batu': 'Labuhanbatu Utara',
    'Labuhan Batu': 'Labuhanbatu',
    'Tulang Bawang': 'Tulangbawang',
    'Lima Puluh': 'Lima Puluh Kota',
    'Central Jakarta': 'Jakarta Pusat',
    'Banggai Islands': 'Banggai Kepulauan',
    'Baru': 'Barru',
    'Baubau': 'Bau-Bau',
}


def build_admin2_map(acled_names, gadm_names):
    """Map ACLED admin2 names to GADM district names.

    Resolution order: direct match -> manual override -> directional-word /
    "X Islands" translation rules. Unresolved names are returned in
    `unmatched` and their events dropped (mostly post-2014 pemekaran
    regencies absent from GADM 4.1).
    """
    mapping, unmatched = {}, []
    for name in acled_names:
        if name in gadm_names:
            mapping[name] = name
            continue
        override = ADMIN2_OVERRIDES.get(name)
        if override and override in gadm_names:
            mapping[name] = override
            continue
        candidates = []
        m = re.match(r'^(.*) Islands$', name)
        if m:
            candidates.append(f'Kepulauan {m.group(1)}')
        for en, idn in DIR_MAP.items():
            m = re.match(rf'^{en} (.*)$', name)
            if m:
                candidates.append(f'{m.group(1)} {idn}')
        found = next((c for c in candidates if c in gadm_names), None)
        if found:
            mapping[name] = found
        else:
            unmatched.append(name)
    return mapping, unmatched


def build_districts_from_gadm(shp_path):
    """One-time: GADM level-2 shapefile -> districts GeoDataFrame artifact."""
    gdf = gpd.read_file(shp_path)[['NAME_1', 'NAME_2', 'geometry']].rename(
        columns={'NAME_1': 'province', 'NAME_2': 'district'})
    # Disambiguate district names duplicated across provinces
    dup = gdf['district'].duplicated(keep=False)
    gdf.loc[dup, 'district'] = (
        gdf.loc[dup, 'district'] + ' (' + gdf.loc[dup, 'province'] + ')')
    rep = gdf.geometry.representative_point()
    gdf['dist_lat'] = rep.y
    gdf['dist_lon'] = rep.x
    # Light simplification (~100 m) so the artifact is small enough to commit;
    # negligible relative to the 375 m fire-pixel resolution.
    gdf['geometry'] = gdf.geometry.simplify(0.001, preserve_topology=True)
    return gdf


def load_districts():
    return gpd.read_parquet(config.DISTRICTS_PARQUET)


def assign_points_to_districts(df, districts_gdf, lat_col='latitude',
                               lon_col='longitude'):
    """Point-in-polygon join; adds a `district` column (NaN if outside)."""
    pts = gpd.GeoDataFrame(
        df, geometry=gpd.points_from_xy(df[lon_col], df[lat_col]),
        crs='EPSG:4326')
    joined = gpd.sjoin(pts, districts_gdf[['district', 'geometry']],
                       how='left', predicate='within')
    # sjoin can duplicate rows if polygons overlap after simplification;
    # keep the first match per original row.
    joined = joined[~joined.index.duplicated(keep='first')]
    out = df.copy()
    out['district'] = joined['district'].reindex(df.index).values
    return out


def aggregate_conflict(acled, districts_gdf):
    """ACLED event-level frame -> district-month outcome counts.

    Expects columns: event_date, admin2, event_type, fatalities.
    """
    acled = acled.copy()
    acled['event_date'] = pd.to_datetime(acled['event_date'])
    acled['year'] = acled['event_date'].dt.year
    acled['month'] = acled['event_date'].dt.month

    names = acled['admin2'].dropna().unique()
    gadm_names = set(districts_gdf['district'])
    mapping, unmatched = build_admin2_map(names, gadm_names)
    acled['district'] = acled['admin2'].map(mapping)
    matched = acled.dropna(subset=['district']).copy()
    print(f'  ACLED crosswalk: {len(mapping)}/{len(names)} districts matched, '
          f'{len(matched):,}/{len(acled):,} events retained '
          f'(unmatched: {sorted(unmatched)[:8]}{"..." if len(unmatched) > 8 else ""})')

    matched['is_pv'] = matched['event_type'].isin(config.POLITICAL_VIOLENCE)
    matched['is_riots_protests'] = matched['event_type'].isin(config.RIOTS_PROTESTS)
    matched['is_battles'] = matched['event_type'].isin(config.BATTLES_VIOLENCE)
    for col, t in config.FOURWAY_TYPES.items():
        matched[f'is_{col}'] = matched['event_type'] == t

    agg = {
        'events': ('event_type', 'count'),
        'pv_events': ('is_pv', 'sum'),
        'fatalities': ('fatalities', 'sum'),
        'riots_protests': ('is_riots_protests', 'sum'),
        'battles_violence': ('is_battles', 'sum'),
    }
    for col in config.FOURWAY_TYPES:
        agg[col] = (f'is_{col}', 'sum')

    out = (matched.groupby(['district', 'year', 'month'])
           .agg(**agg).reset_index())
    count_cols = [c for c in out.columns if c not in ('district', 'year', 'month')]
    out[count_cols] = out[count_cols].astype(int)
    return out


def aggregate_fires(fires, districts_gdf):
    """Fire detections (already district-assigned & filtered) -> district-month FRP."""
    pm = (fires.groupby(['district', 'year', 'month'])
          .agg(n_fires=('frp', 'size'), total_frp=('frp', 'sum'))
          .reset_index())
    pm['log_frp'] = np.log1p(pm['total_frp'])
    return pm
