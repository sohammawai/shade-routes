"""Reusable functions for Shade Routes (Phase 5 version).

Every function works for any area listed in areas.py. Data for each area is
saved in its own folder, for example data/chandni_chowk/.
"""
import os

import mercantile
import numpy as np
import osmnx as ox
import pandas as pd
import geopandas as gpd
import pvlib
import rasterio
from rasterio.errors import RasterioIOError, WindowError
from rasterio.features import shapes
from rasterio.warp import transform_bounds
from rasterio.windows import Window, from_bounds
from shapely.affinity import translate
from shapely.geometry import shape
from shapely.ops import unary_union

from areas import AREAS, DEFAULT_AREA

DATA_DIR = "data"
CANOPY_URL = ("https://dataforgood-fb-data.s3.amazonaws.com/"
              "forests/v1/alsgedi_global_v6_float/chm/{qk}.tif")
CANOPY_ZOOM = 9            # the satellite tiles are named by zoom-9 quadkeys


# ---------- Areas and folders ----------
def get_area(key):
    """The settings for one area, or a clear error if the key is wrong."""
    if key not in AREAS:
        raise ValueError(f"Unknown area '{key}'. Choose one of: {', '.join(AREAS)}")
    return AREAS[key]


def area_dir(key):
    """data/<key>/, created if it does not exist yet."""
    path = os.path.join(DATA_DIR, key)
    os.makedirs(path, exist_ok=True)
    return path


def area_bbox(area):
    """(west, south, east, north) in degrees: a square around the centre."""
    lat, lon = area["center"]
    dlat = area["radius"] / 111_320
    dlon = area["radius"] / (111_320 * np.cos(np.radians(lat)))
    return lon - dlon, lat - dlat, lon + dlon, lat + dlat


def _first_text(value):
    """OSM values can be a list, empty, or text. Always return plain text."""
    if isinstance(value, (list, tuple)):
        value = next((v for v in value if isinstance(v, str) and v.strip()), "")
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return ""
    return str(value).strip()


# ---------- Streets ----------
def load_streets(area):
    """Walking streets in metres, one edge per street segment (no direction)."""
    G = ox.graph_from_point(area["center"], dist=area["radius"], network_type="walk")
    G = ox.project_graph(G)
    return ox.convert.to_undirected(G)


def street_segments(G):
    """One row per street segment: id, end nodes, name, type, length and shape."""
    edges = ox.graph_to_gdfs(G, nodes=False).reset_index()
    edges = edges[edges["u"] != edges["v"]].copy()          # drop loops
    for col in ("name", "highway"):
        if col not in edges.columns:
            edges[col] = ""
        edges[col] = edges[col].apply(_first_text)
    edges = edges.reset_index(drop=True)
    edges["seg_id"] = edges.index
    edges["length"] = edges.geometry.length.round(1)
    return edges[["seg_id", "u", "v", "key", "name", "highway", "length", "geometry"]]


def street_nodes(G):
    """One row per intersection: OSM id and position."""
    nodes = ox.graph_to_gdfs(G, edges=False)
    return nodes[["x", "y", "geometry"]].reset_index()      # adds an 'osmid' column


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


def load_buildings(area, crs):
    """Building outlines with estimated heights, in the given coordinate grid."""
    try:
        b = ox.features_from_point(area["center"], tags={"building": True}, dist=area["radius"])
    except Exception:                                          # no buildings mapped here
        return gpd.GeoDataFrame({"height_m": []}, geometry=[], crs=crs)
    b = b[b.geometry.type.isin(["Polygon", "MultiPolygon"])].copy()
    if "location" in b.columns:
        b = b[b["location"] != "underground"]
    if "layer" in b.columns:
        layer = pd.to_numeric(b["layer"], errors="coerce")
        b = b[~(layer < 0)]
    b["height_m"] = b.apply(_estimate_height, axis=1)
    return b.to_crs(crs)


# ---------- Trees from OpenStreetMap ----------
def load_trees(area, crs, canopy_radius=4.0):
    """Tree shapes from OpenStreetMap. A single tree becomes a circle of canopy_radius metres."""
    tags = {"natural": ["tree", "tree_row", "wood"], "landuse": "forest"}
    try:
        t = ox.features_from_point(area["center"], tags=tags, dist=area["radius"])
    except Exception:
        return gpd.GeoSeries([], crs=crs)                      # no trees mapped here
    t = t.to_crs(crs)
    out = []
    for geom in t.geometry:
        if geom.geom_type in ("Point", "LineString", "MultiLineString"):
            out.append(geom.buffer(canopy_radius, resolution=4))
        else:
            out.append(geom)
    return gpd.GeoSeries(out, crs=crs)


# ---------- Trees from satellite canopy (Meta/WRI, on AWS Open Data) ----------
def _read_canopy_tile(url, bbox, min_height):
    """Canopy shapes from one satellite tile, only inside bbox. None if they don't overlap."""
    west, south, east, north = bbox
    with rasterio.open(url) as src:
        bounds = transform_bounds("EPSG:4326", src.crs, west, south, east, north)
        window = from_bounds(*bounds, transform=src.transform)
        try:
            window = window.intersection(Window(0, 0, src.width, src.height))
        except WindowError:
            return None
        heights = src.read(1, window=window)                   # only this small window
        transform = src.window_transform(window)
        source_crs = src.crs
    heights = np.nan_to_num(heights)
    is_tree = (heights >= min_height).astype("uint8")
    polygons = [shape(g) for g, _ in shapes(is_tree, mask=is_tree == 1, transform=transform)]
    return gpd.GeoDataFrame(geometry=polygons, crs=source_crs)


def load_canopy(key, area, crs, min_height=3.0):
    """Tree canopy from the satellite map. Reads every tile the area touches,
    only on the first run; after that it reads data/<key>/canopy.gpkg."""
    cache = os.path.join(area_dir(key), "canopy.gpkg")
    legacy = os.path.join(DATA_DIR, "canopy.gpkg")              # saved by step4b.py
    if key == DEFAULT_AREA and not os.path.exists(cache) and os.path.exists(legacy):
        os.replace(legacy, cache)
        print(f"  Moved your Phase 4 canopy file to {cache}")
    if os.path.exists(cache):
        return gpd.read_file(cache).geometry.to_crs(crs)

    bbox = area_bbox(area)
    tiles = list(mercantile.tiles(*bbox, CANOPY_ZOOM))
    parts = []
    for i, tile in enumerate(tiles, start=1):
        qk = mercantile.quadkey(tile)
        print(f"  Reading canopy tile {qk} ({i} of {len(tiles)}; first run only, a few minutes)...")
        try:
            part = _read_canopy_tile(CANOPY_URL.format(qk=qk), bbox, min_height)
        except RasterioIOError:
            print(f"  Tile {qk} is not available, skipping it")
            continue
        if part is not None and len(part):
            parts.append(part.to_crs(crs))

    if not parts:
        print("  No satellite canopy found; using OpenStreetMap trees only")
        return gpd.GeoSeries([], crs=crs)
    canopy = gpd.GeoDataFrame(pd.concat(parts, ignore_index=True), crs=crs)
    canopy["geometry"] = canopy.geometry.simplify(1.0)          # smooth pixel edges a little
    canopy = canopy[~canopy.geometry.is_empty]
    canopy.to_file(cache, driver="GPKG")
    print(f"  Saved {len(canopy)} canopy shapes to {cache}")
    return canopy.geometry


def clean_canopy(trees, buildings):
    """Remove canopy that sits on rooftops (the satellite model sometimes mistakes them for trees)."""
    if len(trees) == 0 or len(buildings) == 0:
        return trees
    roofs = unary_union(list(buildings.geometry))
    cleaned = trees.difference(roofs)
    return cleaned[~cleaned.is_empty]


# ---------- Sun and shadows ----------
def sun_position(time_str, lat, lon):
    """Sun elevation and azimuth (degrees) at a local Delhi time."""
    t = pd.DatetimeIndex([pd.Timestamp(time_str, tz="Asia/Kolkata")])
    sp = pvlib.solarposition.get_solarposition(t, lat, lon)
    return float(sp["apparent_elevation"].iloc[0]), float(sp["azimuth"].iloc[0])


def building_shadows(buildings, elevation, azimuth):
    length = np.minimum(buildings["height_m"] / np.tan(np.radians(elevation)), 300)
    dx = -np.sin(np.radians(azimuth)) * length
    dy = -np.cos(np.radians(azimuth)) * length
    out = []
    for geom, x, y in zip(buildings.geometry, dx, dy):
        moved = translate(geom, xoff=x, yoff=y)
        out.append(unary_union([geom, moved]).convex_hull)
    return gpd.GeoSeries(out, crs=buildings.crs)


def shade_layer(buildings, trees_union, time_str, lat, lon):
    """Everything shaded at that time, as one shape. None after sunset (all shade).
    trees_union is all tree canopy merged once, which makes each hour faster."""
    elevation, azimuth = sun_position(time_str, lat, lon)
    if elevation <= 0:
        return None
    shadows = building_shadows(buildings, elevation, azimuth)
    return unary_union(list(shadows) + [trees_union])


# ---------- Street scores ----------
def street_corridors(segments, half_width=4.0):
    """Each street line widened into a strip: road plus footpaths."""
    return segments.geometry.buffer(half_width, cap_style="flat")


def shade_fraction(corridors, shade):
    """For each corridor: the share of its area in shade (0 to 1)."""
    if shade is None:
        return pd.Series(1.0, index=corridors.index)
    shaded = corridors.intersection(shade).area
    return (shaded / corridors.area).fillna(0).clip(0, 1)
