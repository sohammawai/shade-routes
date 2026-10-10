"""Phase 6: simulate walking demand and rank where to plant, for one area or all.

Usage (after build_area.py for that area):
    python priority.py connaught_place
    python priority.py --all

Saves:
    data/<key>/priority.gpkg       segments with demand and priority (open in QGIS if you like)
    data/<key>/priority_map.png    a quick map of where to plant first
    data/web/<key>.json            everything the website needs for this area
    data/web/areas_index.json      the list of areas, for the area selector
"""
import argparse
import json
import os
import time

import geopandas as gpd
import matplotlib.pyplot as plt

from areas import AREAS, HOT_HOURS, HOURS
from demand import (SIMULATED_TRIPS, area_stats, load_trip_points, name_metro_gates,
                    planting_priority, rank_streets, simulate_trips, trip_weights, walking_graph)
from shade_lib import area_dir, get_area

WEB_DIR = os.path.join("data", "web")
RAMP = ["#e99460", "#da7044", "#c55031", "#a03524", "#6f1d19"]   # low to high priority
NO_DEMAND = "#d8d6cf"


def level(score):
    """Priority class 1-5 for colouring; 0 when nobody walks there."""
    return 0 if score <= 0 else min(5, 1 + int(score) // 20)


def export_web(key, area, summary, seg, streets, pois, nodes, G, stats):
    """Write data/web/<key>.json: compact, plain JSON in latitude/longitude."""
    os.makedirs(WEB_DIR, exist_ok=True)
    node_xy = {int(r.osmid): (r.x, r.y) for r in nodes.itertuples(index=False)}
    seg_ll = seg.to_crs(4326)
    shade_cols = [f"shade_{h:02d}" for h in HOURS]

    segments = []
    for s, geom_ll in zip(seg.itertuples(index=False), seg_ll.geometry):
        coords = [[round(x, 6), round(y, 6)] for x, y in geom_ll.coords]
        ux, uy = node_xy[int(s.u)]
        first, last = s.geometry.coords[0], s.geometry.coords[-1]
        if (first[0] - ux) ** 2 + (first[1] - uy) ** 2 > (last[0] - ux) ** 2 + (last[1] - uy) ** 2:
            coords.reverse()                                   # make every line start at node u
        segments.append({
            "id": int(s.seg_id), "u": int(s.u), "v": int(s.v),
            "street": s.street_id, "name": s.label or "Unnamed path", "len": float(s.length),
            "shade": [round(float(getattr(s, c)), 2) for c in shade_cols],
            "demand": int(round(s.demand)), "score": int(s.score), "coords": coords,
        })

    nodes_ll = nodes[nodes["osmid"].isin(list(G.nodes))].to_crs(4326)
    node_list = [[int(r.osmid), round(r.geometry.x, 6), round(r.geometry.y, 6)]
                 for r in nodes_ll.itertuples(index=False)]

    places = []
    named = pois[pois["name"] != ""].to_crs(4326).sort_values("weight", ascending=False)
    for r in named.drop_duplicates("name").head(300).itertuples(index=False):
        places.append({"name": r.name, "kind": r.kind,
                       "lat": round(r.geometry.y, 6), "lon": round(r.geometry.x, 6)})

    street_list = []
    ordered = streets.assign(_order=streets["rank"].where(streets["rank"] > 0, 10 ** 9))
    for r in ordered.sort_values(["_order", "exposure"], ascending=[True, False]).itertuples(index=False):
        street_list.append({
            "id": r.id, "name": r.name, "length": r.length, "demand": int(round(r.demand)),
            "shade": r.shade, "sun_hot": r.sun_hot, "score": int(r.score),
            "rank": int(r.rank) if r.rank else None, "segments": r.segments,
        })

    data = {
        "key": key, "name": area["name"], "center": list(area["center"]),
        "radius": area["radius"], "date": summary["date"], "hours": HOURS, "hot_hours": HOT_HOURS,
        "stats": {**stats, "hourly_shade": summary["hourly_shade"],
                  "street_km": summary["street_km"], "tree_shapes": summary["tree_shapes"]},
        "nodes": node_list, "segments": segments, "streets": street_list, "places": places,
    }
    path = os.path.join(WEB_DIR, f"{key}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, separators=(",", ":"))
    print(f"  Saved {path} ({os.path.getsize(path) / 1e6:.1f} MB)")


def rebuild_index():
    """data/web/areas_index.json lists every area that has website data."""
    areas = []
    for key in AREAS:                                          # keep the order of areas.py
        path = os.path.join(WEB_DIR, f"{key}.json")
        if not os.path.exists(path):
            continue
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        areas.append({"key": d["key"], "name": d["name"], "center": d["center"],
                      "radius": d["radius"], "date": d["date"], "hours": d["hours"],
                      "stats": d["stats"]})
    with open(os.path.join(WEB_DIR, "areas_index.json"), "w", encoding="utf-8") as f:
        json.dump({"areas": areas}, f, indent=1)
    print(f"Updated {WEB_DIR}/areas_index.json ({len(areas)} areas)")


def save_map(key, area, seg, streets, folder, show):
    fig, ax = plt.subplots(figsize=(9, 9))
    levels = seg["score"].apply(level)
    seg[levels == 0].plot(ax=ax, color=NO_DEMAND, linewidth=0.8)
    for lv in range(1, 6):
        part = seg[levels == lv]
        if len(part):
            part.plot(ax=ax, color=RAMP[lv - 1], linewidth=1 + lv * 0.6,
                      label=["Lowest", "Low", "Medium", "High", "Highest"][lv - 1])
    top = streets[streets["rank"].between(1, 5)].sort_values("rank")
    ax.set_title(f"{area['name']}: where to plant first\nTop street: {top['name'].iloc[0]}"
                 if len(top) else area["name"], loc="left")
    ax.legend(title="Planting priority", frameon=False, loc="lower right")
    ax.set_axis_off()
    fig.tight_layout()
    fig.savefig(os.path.join(folder, "priority_map.png"), dpi=150)
    if show:
        plt.show()
    plt.close(fig)


def run(key, show=True):
    area = get_area(key)
    folder = area_dir(key)
    seg_path = os.path.join(folder, "segments.gpkg")
    if not os.path.exists(seg_path):
        raise SystemExit(f"No shade data for {key}. Run first: python build_area.py {key}")
    start = time.perf_counter()
    print(f"\n=== {area['name']} ({key}) ===")

    segments = gpd.read_file(seg_path)
    nodes = gpd.read_file(os.path.join(folder, "nodes.gpkg"))
    with open(os.path.join(folder, "summary.json"), encoding="utf-8") as f:
        summary = json.load(f)
    crs = segments.crs

    G = walking_graph(segments)
    pois = name_metro_gates(load_trip_points(area, crs))
    counts = pois["kind"].value_counts().to_dict()
    print(f"  {len(pois)} places people walk to: " + ", ".join(f"{v} {k}" for k, v in counts.items()))
    weights = trip_weights(pois, nodes, G)
    usage = simulate_trips(G, weights)
    print(f"  Simulated {SIMULATED_TRIPS:,} walking trips")

    seg = planting_priority(segments, usage)
    seg, streets = rank_streets(seg, pois)
    stats = area_stats(seg)
    stats["streets_ranked"] = int((streets["rank"] > 0).sum())
    print(f"  Busy streets with under 20% shade at 2pm: {stats['busy_low_shade_share']:.0%} of their length")

    print(f"\n  {'#':>2}  {'Street':32} {'score':>5} {'trips/day':>9} {'shade 2pm':>9}")
    hour_2pm = HOURS.index(14)
    for r in streets[streets["rank"].between(1, 10)].sort_values("rank").itertuples():
        print(f"  {r.rank:>2}  {r.name[:32]:32} {r.score:>5} {r.demand:>9,.0f} {r.shade[hour_2pm]:>9.0%}")

    seg.to_file(os.path.join(folder, "priority.gpkg"), driver="GPKG")
    export_web(key, area, summary, seg, streets, pois, nodes, G, stats)
    save_map(key, area, seg, streets, folder, show)
    print(f"  Done in {time.perf_counter() - start:.0f} seconds")


def main():
    parser = argparse.ArgumentParser(description="Simulate walking demand and planting priority.")
    parser.add_argument("areas", nargs="*", help="area keys from areas.py")
    parser.add_argument("--all", action="store_true", help="every area that has shade data")
    parser.add_argument("--no-show", action="store_true", help="save maps without opening them")
    args = parser.parse_args()

    if args.all:
        keys = [k for k in AREAS if os.path.exists(os.path.join("data", k, "segments.gpkg"))]
    else:
        keys = args.areas
    if not keys:
        parser.error(f"name at least one area, or use --all. Areas: {', '.join(AREAS)}")
    for key in keys:
        run(key, show=not (args.no_show or args.all))
    rebuild_index()


if __name__ == "__main__":
    main()
