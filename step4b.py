import time
import pandas as pd
import geopandas as gpd
import matplotlib.pyplot as plt
import osmnx as ox
from shade_lib import (load_streets, load_buildings, load_trees, load_canopy,
                       shade_layer, edge_shade_fraction)

start = time.perf_counter()

G = load_streets()
crs = G.graph["crs"]
buildings = load_buildings(crs)
osm_trees = load_trees(crs)
canopy = load_canopy(crs)
all_trees = gpd.GeoSeries(pd.concat([osm_trees, canopy], ignore_index=True), crs=crs)
print(f"{len(osm_trees)} OSM tree features, {len(canopy)} satellite canopy shapes")

edges = ox.graph_to_gdfs(G, nodes=False)
lengths = edges.geometry.length
t = "2026-05-15 14:00"

fig, axes = plt.subplots(1, 2, figsize=(15, 7))
for ax, (label, trees) in zip(axes, [("OSM trees only", osm_trees),
                                     ("OSM + satellite canopy", all_trees)]):
    shade = shade_layer(buildings, trees, t)
    edges["shade"] = edge_shade_fraction(edges, shade)
    avg = (edges["shade"] * lengths).sum() / lengths.sum()
    print(f"{label}: average street shade at 2pm {avg:.0%}")

    buildings.plot(ax=ax, color="lightgray")
    if len(trees):
        trees.plot(ax=ax, color="green", alpha=0.3)
    edges.plot(ax=ax, column="shade", cmap="RdYlGn", vmin=0, vmax=1,
               linewidth=2, legend=True)
    ax.set_title(f"{label}, {t}\nAverage street shade: {avg:.0%}")
    ax.set_axis_off()

plt.tight_layout()
print(f"Finished in {time.perf_counter() - start:.1f} seconds")
plt.show()