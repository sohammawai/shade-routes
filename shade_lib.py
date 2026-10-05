"""Reusable functions for the Shade Routes project."""
import os
import osmnx as ox
import pandas as pd
import numpy as np
import geopandas as gpd
import pvlib
import mercantile
import rasterio
from rasterio.windows import from_bounds
from rasterio.warp import transform_bounds
from rasterio.features import shapes
from shapely.geometry import shape
from shapely.affinity import translate
from shapely.ops import unary_union

PLACE = (28.6315, 77.2167)  # Connaught Place
DIST = 800                  # metres around the centre


# ---------- Streets ----------
def load_streets():
    """Walking street graph, converted to metres."""
    G = ox.graph_from_point(PLACE, dist=DIST, network_type="walk")
    return ox.project_graph(G)


# ---------- Buildings ----------
def _estimate_height(row):
    h = row.get("height")
    if pd.notna(h):
        try:
            return float(str(h).replace("m", "").strip())
        except ValueError:
            pass
    levels = row.get("building:levels")
    if pd.notna(levels):
        try:
            return float(levels) * 3.2
        except ValueError:
            pass
    return 10.0


def load_buildings(crs):
    """Building outlines with estimated heights, in the given coordinate grid."""
    b = ox.features_from_point(PLACE, tags={"building": True}, dist=DIST)
    b = b[b.geometry.type.isin(["Polygon", "MultiPolygon"])].copy()
    if "location" in b.columns:
        b = b[b["location"] != "underground"]
    if "layer" in b.columns:
        layer = pd.to_numeric(b["layer"], errors="coerce")
        b = b[~(layer < 0)]
    b["height_m"] = b.apply(_estimate_height, axis=1)
    return b.to_crs(crs)


# ---------- Trees from OpenStreetMap ----------
def load_trees(crs, canopy_radius=4.0):
    """Tree canopy shapes from OpenStreetMap.
    Single trees and tree rows become circles/strips of canopy_radius metres."""
    tags = {"natural": ["tree", "tree_row", "wood"], "landuse": "forest"}
    try:
        t = ox.features_from_point(PLACE, tags=tags, dist=DIST)
    except Exception:
        return gpd.GeoSeries([], crs=crs)  # no trees mapped in this area
    t = t.to_crs(crs)
    shapes_list = []
    for geom in t.geometry:
        if geom.geom_type in ("Point", "LineString", "MultiLineString"):
            shapes_list.append(geom.buffer(canopy_radius, resolution=4))
        else:
            shapes_list.append(geom)
    return gpd.GeoSeries(shapes_list, crs=crs)


# ---------- Trees from satellite canopy (Meta/WRI, on AWS Open Data) ----------
CANOPY_URL = ("https://dataforgood-fb-data.s3.amazonaws.com/"
              "forests/v1/alsgedi_global_v6_float/chm/{qk}.tif")
CANOPY_CACHE = "data/canopy.gpkg"


def load_canopy(crs, min_height=3.0):
    """Tree canopy shapes from the Meta/WRI canopy height map.
    Anything at least min_height metres tall counts as canopy.
    Downloads only on the first run; after that it reads the saved file."""
    if os.path.exists(CANOPY_CACHE):
        return gpd.read_file(CANOPY_CACHE).geometry.to_crs(crs)

    lat, lon = PLACE
    # Which satellite tile covers CP (tiles are named by a "quadkey" at zoom 9)
    qk = mercantile.quadkey(mercantile.tile(lon, lat, 9))
    print(f"Reading canopy tile {qk} (first run only, can take a few minutes)...")

    # A box around CP, the same size as DIST
    dlat = DIST / 111_320
    dlon = DIST / (111_320 * np.cos(np.radians(lat)))
    west, south, east, north = lon - dlon, lat - dlat, lon + dlon, lat + dlat

    with rasterio.open(CANOPY_URL.format(qk=qk)) as src:
        bounds = transform_bounds("EPSG:4326", src.crs, west, south, east, north)
        window = from_bounds(*bounds, transform=src.transform)
        heights = src.read(1, window=window)          # only this small window
        window_transform = src.window_transform(window)
        source_crs = src.crs

    heights = np.nan_to_num(heights)
    is_tree = (heights >= min_height).astype("uint8")
    print(f"Tree canopy covers {is_tree.mean():.0%} of the area")

    # Turn clusters of tree pixels into shapes
    polygons = [shape(geom) for geom, _ in
                shapes(is_tree, mask=is_tree == 1, transform=window_transform)]
    canopy = gpd.GeoDataFrame(geometry=polygons, crs=source_crs).to_crs(crs)
    canopy["geometry"] = canopy.geometry.simplify(1.0)  # smooth pixel edges a little

    os.makedirs("data", exist_ok=True)
    canopy.to_file(CANOPY_CACHE, driver="GPKG")
    print(f"Saved {len(canopy)} canopy shapes to {CANOPY_CACHE}")
    return canopy.geometry


# ---------- Sun and shadows ----------
def sun_position(time_str):
    """Sun elevation and azimuth (degrees) at a local Delhi time."""
    t = pd.DatetimeIndex([pd.Timestamp(time_str, tz="Asia/Kolkata")])
    sp = pvlib.solarposition.get_solarposition(t, PLACE[0], PLACE[1])
    return float(sp["apparent_elevation"].iloc[0]), float(sp["azimuth"].iloc[0])


def building_shadows(buildings, elevation, azimuth):
    length = np.minimum(buildings["height_m"] / np.tan(np.radians(elevation)), 300)
    dx = -np.sin(np.radians(azimuth)) * length
    dy = -np.cos(np.radians(azimuth)) * length
    shadow_shapes = []
    for geom, x, y in zip(buildings.geometry, dx, dy):
        moved = translate(geom, xoff=x, yoff=y)
        shadow_shapes.append(unary_union([geom, moved]).convex_hull)
    return gpd.GeoSeries(shadow_shapes, crs=buildings.crs)


def shade_layer(buildings, trees, time_str):
    """One combined shape of everything shaded at that time.
    Returns None after sunset (everything is shaded)."""
    elevation, azimuth = sun_position(time_str)
    if elevation <= 0:
        return None
    shadows = building_shadows(buildings, elevation, azimuth)
    return unary_union(list(shadows) + list(trees))


# ---------- Street scores ----------
def edge_shade_fraction(edges, shade):
    """For each street segment: the fraction of its length in shade (0 to 1)."""
    if shade is None:
        return pd.Series(1.0, index=edges.index)
    shaded_length = edges.geometry.intersection(shade).length
    return (shaded_length / edges.geometry.length).fillna(0).clip(0, 1)