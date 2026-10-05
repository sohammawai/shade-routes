"""Reusable functions for the Shade Routes project."""
import osmnx as ox
import pandas as pd
import numpy as np
import geopandas as gpd
import pvlib
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


# ---------- Trees ----------
def load_trees(crs, canopy_radius=4.0):
    """Tree canopy shapes from OpenStreetMap.
    Single trees and tree rows are turned into circles/strips of canopy_radius metres."""
    tags = {"natural": ["tree", "tree_row", "wood"], "landuse": "forest"}
    try:
        t = ox.features_from_point(PLACE, tags=tags, dist=DIST)
    except Exception:
        return gpd.GeoSeries([], crs=crs)  # no trees mapped in this area
    t = t.to_crs(crs)
    shapes = []
    for geom in t.geometry:
        if geom.geom_type in ("Point", "LineString", "MultiLineString"):
            shapes.append(geom.buffer(canopy_radius, resolution=4))
        else:
            shapes.append(geom)
    return gpd.GeoSeries(shapes, crs=crs)


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
    shapes = []
    for geom, x, y in zip(buildings.geometry, dx, dy):
        moved = translate(geom, xoff=x, yoff=y)
        shapes.append(unary_union([geom, moved]).convex_hull)
    return gpd.GeoSeries(shapes, crs=buildings.crs)


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