import osmnx as ox
import pandas as pd
import matplotlib.pyplot as plt

place = (28.6315, 77.2167)
DIST = 800

# Streets (same as step 1, loads from cache now)
G = ox.graph_from_point(place, dist=DIST, network_type="walk")

# Buildings: every mapped building outline in the same area
buildings = ox.features_from_point(place, tags={"building": True}, dist=DIST)

# Keep only real shapes (some entries are just points)
buildings = buildings[buildings.geometry.type.isin(["Polygon", "MultiPolygon"])].copy()


def estimate_height(row):
    """Return building height in metres, and where the number came from."""
    # 1. Actual height tag, e.g. "24" or "24 m"
    h = row.get("height")
    if pd.notna(h):
        try:
            return float(str(h).replace("m", "").strip()), "height tag"
        except ValueError:
            pass
    # 2. Number of floors x ~3.2 m per floor
    levels = row.get("building:levels")
    if pd.notna(levels):
        try:
            return float(levels) * 3.2, "floors tag"
        except ValueError:
            pass
    # 3. No info: assume a typical 3-floor building
    return 10.0, "default guess"


results = buildings.apply(estimate_height, axis=1)
buildings["height_m"] = [r[0] for r in results]
buildings["height_source"] = [r[1] for r in results]

print(f"Found {len(buildings)} buildings")
print(buildings["height_source"].value_counts())
print(f"Tallest: {buildings['height_m'].max():.0f} m")

# Draw streets, then buildings coloured by height on top
fig, ax = ox.plot_graph(G, show=False, close=False, node_size=0,
                        edge_color="lightgray", bgcolor="white")
buildings.plot(ax=ax, column="height_m", cmap="viridis", legend=True, alpha=0.8)
ax.set_title("Connaught Place: buildings coloured by height (m)")
plt.show()