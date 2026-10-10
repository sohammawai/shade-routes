"""Phase 5: build hourly shade data for one area, or for every area.

Usage (from the shade-routes folder):
    python build_area.py connaught_place
    python build_area.py chandni_chowk nehru_place
    python build_area.py --all

Saves, for each area, in data/<key>/:
    segments.gpkg   every street segment with its shade for each hour
    nodes.gpkg      every intersection (needed for routing)
    summary.json    hourly average shade and a few counts
"""
import argparse
import json
import os
import time

import geopandas as gpd
import pandas as pd
from shapely.ops import unary_union

from areas import AREAS, DATE, HOURS
from shade_lib import (area_dir, clean_canopy, get_area, load_buildings, load_canopy,
                       load_streets, load_trees, shade_fraction, shade_layer,
                       street_corridors, street_nodes, street_segments)

HALF_WIDTH = 4.0      # metres either side of the centre line (8 m corridor)


def build(key):
    area = get_area(key)
    folder = area_dir(key)
    lat, lon = area["center"]
    start = time.perf_counter()
    print(f"\n=== {area['name']} ({key}) ===")

    G = load_streets(area)
    crs = G.graph["crs"]
    segments = street_segments(G)
    nodes = street_nodes(G)
    print(f"  {len(segments)} street segments, {len(nodes)} intersections")

    buildings = load_buildings(area, crs)
    raw_trees = gpd.GeoSeries(pd.concat([load_trees(area, crs), load_canopy(key, area, crs)],
                                        ignore_index=True), crs=crs)
    trees = clean_canopy(raw_trees, buildings)
    trees_union = unary_union(list(trees))
    print(f"  {len(buildings)} buildings; tree shapes {len(raw_trees)} before, "
          f"{len(trees)} after removing rooftop errors")

    corridors = street_corridors(segments, HALF_WIDTH)
    lengths = segments["length"]
    hourly = {}
    for h in HOURS:
        shade = shade_layer(buildings, trees_union, f"{DATE} {h:02d}:00", lat, lon)
        frac = shade_fraction(corridors, shade)
        segments[f"shade_{h:02d}"] = frac.round(3)
        hourly[h] = round(float((frac * lengths).sum() / lengths.sum()), 3)
        print(f"  {h:02d}:00  average street shade {hourly[h]:.0%}")

    segments.to_file(os.path.join(folder, "segments.gpkg"), driver="GPKG")
    nodes.to_file(os.path.join(folder, "nodes.gpkg"), driver="GPKG")
    summary = {
        "key": key,
        "name": area["name"],
        "center": list(area["center"]),
        "radius": area["radius"],
        "date": DATE,
        "hours": HOURS,
        "hourly_shade": [hourly[h] for h in HOURS],
        "segments": int(len(segments)),
        "street_km": round(float(lengths.sum()) / 1000, 1),
        "buildings": int(len(buildings)),
        "tree_shapes": int(len(trees)),
    }
    with open(os.path.join(folder, "summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)
    print(f"  Saved {folder}/ in {time.perf_counter() - start:.0f} seconds")
    return summary


def main():
    parser = argparse.ArgumentParser(description="Build hourly shade data for areas.")
    parser.add_argument("areas", nargs="*", help="area keys from areas.py")
    parser.add_argument("--all", action="store_true", help="build every area")
    args = parser.parse_args()

    keys = list(AREAS) if args.all else args.areas
    if not keys:
        parser.error(f"name at least one area, or use --all. Areas: {', '.join(AREAS)}")
    for key in keys:
        build(key)


if __name__ == "__main__":
    main()
