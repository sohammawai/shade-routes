import matplotlib.pyplot as plt
import osmnx as ox
from shade_lib import load_streets, load_buildings, load_trees, shade_layer, edge_shade_fraction
import time
start = time.perf_counter()
G = load_streets()
crs = G.graph["crs"]
buildings = load_buildings(crs)
trees = load_trees(crs)
print(f"{len(buildings)} buildings, {len(trees)} tree features from OpenStreetMap")

# Street segments as a table with one row per segment
edges = ox.graph_to_gdfs(G, nodes=False)

times = ["2026-05-15 08:00", "2026-05-15 14:00"]
fig, axes = plt.subplots(1, 2, figsize=(15, 7))

for ax, t in zip(axes, times):
    shade = shade_layer(buildings, trees, t)
    edges["shade"] = edge_shade_fraction(edges, shade)

    # Average shade, weighted by street length
    lengths = edges.geometry.length
    avg = (edges["shade"] * lengths).sum() / lengths.sum()
    print(f"{t}: average street shade {avg:.0%}")

    buildings.plot(ax=ax, color="lightgray")
    if len(trees):
        trees.plot(ax=ax, color="green", alpha=0.3)
    edges.plot(ax=ax, column="shade", cmap="RdYlGn", vmin=0, vmax=1,
               linewidth=2, legend=True)
    ax.set_title(f"{t}\nAverage street shade: {avg:.0%}")
    ax.set_axis_off()

plt.tight_layout()
print(f"Finished in {time.perf_counter() - start:.1f} seconds")
plt.show()