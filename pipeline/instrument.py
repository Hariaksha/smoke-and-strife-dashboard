"""Upwind fire-exposure instrument construction.

Ported from analysis/wind-IV.ipynb cell 6 (vectorised cKDTree version).
For each district-month: Z_dt = sum of FRP over fire detections within
UPWIND_RADIUS_KM of the district centroid AND within +/- UPWIND_HALF_ANG
degrees of the direction the district's wind blows from.
"""
import numpy as np
import pandas as pd
from scipy.spatial import cKDTree

from . import config


def haversine_vectorised(lat1, lon1, lat2_arr, lon2_arr):
    dlat = np.radians(lat2_arr - lat1)
    dlon = np.radians(lon2_arr - lon1)
    a = (np.sin(dlat / 2) ** 2 +
         np.cos(np.radians(lat1)) * np.cos(np.radians(lat2_arr)) *
         np.sin(dlon / 2) ** 2)
    return config.R_EARTH * 2 * np.arcsin(np.sqrt(a))


def bearing_to(lat1, lon1, lat2_arr, lon2_arr):
    dlon = np.radians(lon2_arr - lon1)
    x = np.sin(dlon) * np.cos(np.radians(lat2_arr))
    y = (np.cos(np.radians(lat1)) * np.sin(np.radians(lat2_arr)) -
         np.sin(np.radians(lat1)) * np.cos(np.radians(lat2_arr)) * np.cos(dlon))
    return (np.degrees(np.arctan2(x, y)) + 360) % 360


def angular_diff(a, b):
    return np.minimum(np.abs(a - b) % 360, 360 - np.abs(a - b) % 360)


def lat_lon_to_cartesian(lat, lon):
    lat_rad, lon_rad = np.radians(lat), np.radians(lon)
    return np.column_stack([
        np.cos(lat_rad) * np.cos(lon_rad),
        np.cos(lat_rad) * np.sin(lon_rad),
        np.sin(lat_rad),
    ])


def build_instrument(fires, district_wind, districts_gdf):
    """Compute the upwind instrument for every row of `district_wind`.

    fires: detection-level frame with latitude, longitude, frp, year, month
           (already filtered to vegetation fires / h,n confidence and
           assigned to a district).
    district_wind: district-month frame with wind_dir_from.
    Returns district-month frame with upwind_frp / n_upwind_fires.
    """
    fire_lats = fires['latitude'].to_numpy()
    fire_lons = fires['longitude'].to_numpy()
    fire_frps = fires['frp'].to_numpy()
    fire_ym = (fires['year'] * 100 + fires['month']).to_numpy()

    fire_tree = cKDTree(lat_lon_to_cartesian(fire_lats, fire_lons))
    centroids = districts_gdf.set_index('district')[['dist_lat', 'dist_lon']]

    radius_radians = config.UPWIND_RADIUS_KM / config.R_EARTH
    max_chord = 2 * np.sin(radius_radians / 2)

    rows = []
    for i, dw in enumerate(district_wind.itertuples(index=False)):
        if (i + 1) % 10000 == 0:
            print(f'    instrument progress: {i + 1:,}/{len(district_wind):,}')
        dname, yr, mo = dw.district, int(dw.year), int(dw.month)
        wdir = dw.wind_dir_from
        clat, clon = centroids.loc[dname, 'dist_lat'], centroids.loc[dname, 'dist_lon']

        zero = {'district': dname, 'year': yr, 'month': mo,
                'upwind_frp': 0.0, 'n_upwind_fires': 0}
        c_cart = lat_lon_to_cartesian(np.array([clat]), np.array([clon]))[0]
        idx = fire_tree.query_ball_point(c_cart, max_chord)
        if not idx:
            rows.append(zero)
            continue
        idx = np.array(idx)
        idx = idx[fire_ym[idx] == yr * 100 + mo]
        if len(idx) == 0:
            rows.append(zero)
            continue
        f_lats, f_lons, f_frps = fire_lats[idx], fire_lons[idx], fire_frps[idx]
        dists = haversine_vectorised(clat, clon, f_lats, f_lons)
        near = dists <= config.UPWIND_RADIUS_KM
        if not near.any():
            rows.append(zero)
            continue
        bearings = bearing_to(clat, clon, f_lats[near], f_lons[near])
        upwind = angular_diff(bearings, wdir) <= config.UPWIND_HALF_ANG
        rows.append({'district': dname, 'year': yr, 'month': mo,
                     'upwind_frp': float(f_frps[near][upwind].sum()),
                     'n_upwind_fires': int(upwind.sum())})

    out = pd.DataFrame(rows)
    out['log_upwind_frp'] = np.log1p(out['upwind_frp'])
    return out
