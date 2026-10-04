import osmnx as ox
import pandas as pd
import numpy as np
import geopandas as gpd
import matplotlib.pyplot as plt
import pvlib
from shapely.affinity import translate
from shapely.ops import unary_union

place = (28.6315, 77.2167)
DIST = 800

# ---------- Load streets and buildings ----------
G = ox.graph_from_point(place, dist=DIST, network_type="walk")
buildings = ox.features_from_point(place, tags={"building": True}, dist=DIST)
buildings = buildings[buildings.geometry.type.isin(["Polygon", "MultiPolygon"])].copy()

# Remove underground structures (like Palika Bazaar), they cast no shadow
if "location" in buildings.columns:
    buildings = buildings[buildings["location"] != "underground"]
if "layer" in buildings.columns:
    layer = pd.to_numeric(buildings["layer"], errors="coerce")
    buildings = buildings[~(layer < 0)]


def estimate_height(row):
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


buildings["height_m"] = buildings.apply(estimate_height, axis=1)

# Convert from latitude/longitude to metres so shadow lengths make sense
G_proj = ox.project_graph(G)
buildings = buildings.to_crs(G_proj.graph["crs"])


# ---------- Sun position ----------
def sun_position(time_str):
    """Sun elevation (degrees above horizon) and azimuth (degrees clockwise from north)."""
    t = pd.DatetimeIndex([pd.Timestamp(time_str, tz="Asia/Kolkata")])
    sp = pvlib.solarposition.get_solarposition(t, place[0], place[1])
    return float(sp["apparent_elevation"].iloc[0]), float(sp["azimuth"].iloc[0])


# ---------- Shadows ----------
def compute_shadows(buildings, elevation, azimuth):
    if elevation <= 0:
        return None  # sun has set, everything is in shade
    # Shadow length = height / tan(elevation), capped so sunrise/sunset shadows stay sane
    length = np.minimum(buildings["height_m"] / np.tan(np.radians(elevation)), 300)
    # Shadows point AWAY from the sun
    dx = -np.sin(np.radians(azimuth)) * length
    dy = -np.cos(np.radians(azimuth)) * length
    shapes = []
    for geom, x, y in zip(buildings.geometry, dx, dy):
        moved = translate(geom, xoff=x, yoff=y)
        # Join the building and its moved copy to get the shadow shape
        shapes.append(unary_union([geom, moved]).convex_hull)
    return gpd.GeoSeries(shapes, crs=buildings.crs)


# ---------- Compare morning vs afternoon ----------
times = ["2026-05-15 08:00", "2026-05-15 14:00"]
fig, axes = plt.subplots(1, 2, figsize=(14, 7))

for ax, t in zip(axes, times):
    elev, az = sun_position(t)
    shadows = compute_shadows(buildings, elev, az)
    ox.plot_graph(G_proj, ax=ax, show=False, close=False, node_size=0,
                  edge_color="lightgray", bgcolor="white")
    if shadows is not None:
        shadows.plot(ax=ax, color="black", alpha=0.35)
    buildings.plot(ax=ax, color="saddlebrown")
    ax.set_title(f"{t}\nSun elevation {elev:.0f}°, azimuth {az:.0f}°")

plt.tight_layout()
plt.show()