import os
import time
import pandas as pd
import geopandas as gpd
import networkx as nx
import osmnx as ox
import matplotlib.pyplot as plt
from shade_lib import (load_streets, load_buildings, load_trees, load_canopy,
                       clean_canopy, shade_layer, edge_shade_fraction)

HALF_WIDTH = 4.0          # metres either side of the centre line (8 m corridor)
HOURS = range(8, 19)      # 8am to 6pm
DATE = "2026-05-15"

start = time.perf_counter()
G = load_streets()
crs = G.graph["crs"]
buildings = load_buildings(crs)

raw_trees = gpd.GeoSeries(pd.concat([load_trees(crs), load_canopy(crs)],
                                    ignore_index=True), crs=crs)
trees = clean_canopy(raw_trees, buildings)
print(f"Tree shapes: {len(raw_trees)} before, {len(trees)} after removing rooftop errors")

edges = ox.graph_to_gdfs(G, nodes=False)
lengths = edges.geometry.length
averages = []

for h in HOURS:
    col = f"shade_{h:02d}"
    shade = shade_layer(buildings, trees, f"{DATE} {h:02d}:00")
    frac = edge_shade_fraction(edges, shade, half_width=HALF_WIDTH)

    edges[col] = frac                                # for the saved map file
    nx.set_edge_attributes(G, frac.to_dict(), col)   # for routing later

    avg = (frac * lengths).sum() / lengths.sum()
    averages.append(avg)
    print(f"{h:02d}:00  average street shade {avg:.0%}")

# ---------- Save the results ----------
os.makedirs("data", exist_ok=True)
ox.save_graphml(G, "data/shade_graph.graphml")       # the routing graph, with shade

shade_cols = [f"shade_{h:02d}" for h in HOURS]
out = edges.reset_index()[["u", "v", "key", "geometry"] + shade_cols]
if "name" in edges.columns:
    out["name"] = edges["name"].astype(str).values
out.to_file("data/street_shade.gpkg", driver="GPKG")  # for maps and the planner

print("Saved data/shade_graph.graphml and data/street_shade.gpkg")
print(f"Finished in {time.perf_counter() - start:.1f} seconds")

# ---------- Shade through the day ----------
plt.figure(figsize=(9, 5))
plt.plot([f"{h}:00" for h in HOURS], [a * 100 for a in averages], marker="o")
plt.ylim(0, 100)
plt.ylabel("Average street shade (%)")
plt.title(f"Connaught Place: street shade through the day ({DATE})")
plt.grid(alpha=0.3)
plt.show()