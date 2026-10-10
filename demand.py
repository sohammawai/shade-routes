"""Phase 6: estimate where people walk, and turn shade + walking into planting priority.

Nobody publishes footfall for Delhi's streets, so we simulate it: thousands of
walking trips between places people actually go (metro gates, bus stops,
markets, offices, colleges), each following the shortest walking path.
Counting the trips on each street segment gives its demand.
"""
import hashlib
import re
from collections import Counter, defaultdict

import geopandas as gpd
import networkx as nx
import numpy as np
import osmnx as ox
import pandas as pd

from areas import HOT_HOURS, HOURS

# ---------- Settings: assumptions you can tune, and should state in the pitch ----------
SIMULATED_TRIPS = 20_000    # walking trips simulated per area per day
MAX_WALK_M = 1500           # longest walk considered inside an area
DISTANCE_DECAY_M = 700      # short trips are more common than long ones
MAX_SNAP_M = 150            # a place further than this from any street is ignored
MIN_RANK_LENGTH_M = 50      # streets shorter than this are left out of the ranking
STRETCH_M = 200             # a street is ranked by its busiest 200 m
BUSY_QUANTILE = 0.75        # "busy" = the top 25% of segments by walking demand
SEED = 42                   # the same random trips on every run

# Places people walk to and from, as OpenStreetMap tags
POI_TAGS = {
    "railway": ["subway_entrance", "station"],
    "public_transport": ["station"],
    "highway": ["bus_stop"],
    "amenity": ["marketplace", "college", "university", "school", "hospital", "clinic",
                "bank", "post_office", "library", "cinema", "place_of_worship",
                "restaurant", "cafe", "fast_food", "food_court"],
    "shop": True,
    "office": True,
    "tourism": ["attraction", "museum", "hotel"],
    "leisure": ["park"],
}


def _text(value):
    if isinstance(value, (list, tuple)):
        value = value[0] if value else ""
    return value.strip() if isinstance(value, str) else ""


def classify_poi(row):
    """(weight, kind) for one place. The first rule that matches wins."""
    def tag(name):
        return _text(row.get(name))

    amenity = tag("amenity")
    if tag("railway") == "subway_entrance":
        return 40, "metro"
    if tag("railway") == "station" or tag("public_transport") == "station":
        return 20, "metro"
    if tag("highway") == "bus_stop":
        return 6, "bus"
    if amenity in ("college", "university"):
        return 10, "education"
    if amenity in ("marketplace", "hospital"):
        return 8, amenity
    if amenity == "school":
        return 5, "education"
    if amenity in ("clinic", "bank", "post_office", "library", "cinema", "place_of_worship"):
        return 3, "amenity"
    if amenity in ("restaurant", "cafe", "fast_food", "food_court"):
        return 2, "food"
    if tag("tourism"):
        return 3, "landmark"
    if tag("office"):
        return 2, "office"
    if tag("shop"):
        return 1, "shop"
    if tag("leisure") == "park":
        return 2, "park"
    return 0, "other"


def load_trip_points(area, crs):
    """Places people walk between, each with a weight (how many trips it attracts)."""
    columns = ["weight", "kind", "name", "ref", "geometry"]
    try:
        pois = ox.features_from_point(area["center"], tags=POI_TAGS, dist=area["radius"])
    except Exception:
        return gpd.GeoDataFrame(columns=columns, geometry="geometry", crs=crs)
    pois = pois.to_crs(crs)
    classified = pois.apply(classify_poi, axis=1)
    pois["weight"] = [c[0] for c in classified]
    pois["kind"] = [c[1] for c in classified]
    for col in ("name", "ref"):
        pois[col] = pois[col].apply(_text) if col in pois.columns else ""
    pois = pois[pois["weight"] > 0].copy()
    pois["geometry"] = pois.geometry.representative_point()    # one point per place
    return gpd.GeoDataFrame(pois[columns].reset_index(drop=True), geometry="geometry", crs=crs)


def walking_graph(segments):
    """A simple graph for routing: one edge per pair of intersections (the shortest)."""
    G = nx.Graph()
    for s in segments.itertuples(index=False):
        u, v = int(s.u), int(s.v)
        if u == v:
            continue
        if G.has_edge(u, v) and G[u][v]["length"] <= s.length:
            continue
        G.add_edge(u, v, length=float(s.length), seg=int(s.seg_id))
    return G


def nearest_node_ids(points, nodes):
    """For each point, the nearest intersection and its distance in metres."""
    ids = nodes["osmid"].to_numpy()
    nx_, ny_ = nodes["x"].to_numpy(), nodes["y"].to_numpy()
    px, py = points.geometry.x.to_numpy(), points.geometry.y.to_numpy()
    out_ids = np.empty(len(px), dtype=ids.dtype)
    out_dist = np.empty(len(px))
    for start in range(0, len(px), 500):                  # in chunks, to save memory
        dx = px[start:start + 500, None] - nx_[None, :]
        dy = py[start:start + 500, None] - ny_[None, :]
        d2 = dx * dx + dy * dy
        j = d2.argmin(axis=1)
        out_ids[start:start + 500] = ids[j]
        out_dist[start:start + 500] = np.sqrt(d2[np.arange(len(j)), j])
    return out_ids, out_dist


def trip_weights(pois, nodes, G):
    """Add up the weight of every place onto its nearest intersection."""
    nodes = nodes[nodes["osmid"].isin(list(G.nodes))]
    weights = defaultdict(float)
    if len(pois):
        ids, dist = nearest_node_ids(pois, nodes)
        for nid, d, w in zip(ids, dist, pois["weight"]):
            if d <= MAX_SNAP_M:
                weights[int(nid)] += float(w)
    if not weights:                                        # no places found: spread trips evenly
        return {int(n): 1.0 for n in G.nodes}
    return dict(weights)


def simulate_trips(G, node_weights, n_trips=SIMULATED_TRIPS, seed=SEED):
    """Simulate walking trips and count how many use each street segment."""
    rng = np.random.default_rng(seed)
    places = list(node_weights)
    w = np.array([node_weights[n] for n in places], dtype=float)
    origins = rng.choice(len(places), size=n_trips, p=w / w.sum())
    usage = defaultdict(float)
    for oi, count in Counter(origins.tolist()).items():
        origin = places[oi]
        dist, paths = nx.single_source_dijkstra(G, origin, cutoff=MAX_WALK_M, weight="length")
        cand = [i for i, n in enumerate(places) if n != origin and n in dist]
        if not cand:
            continue
        p = np.array([w[i] * np.exp(-dist[places[i]] / DISTANCE_DECAY_M) for i in cand])
        picks = rng.choice(len(cand), size=count, p=p / p.sum())
        for ci, n in Counter(picks.tolist()).items():
            path = paths[places[cand[ci]]]
            for a, b in zip(path[:-1], path[1:]):
                usage[G[a][b]["seg"]] += n
    return usage


def planting_priority(segments, usage):
    """Demand, sun exposure in the hot hours, and a 0-100 priority score per segment."""
    seg = segments.copy()
    seg["demand"] = seg["seg_id"].map(usage).fillna(0.0)
    hot_cols = [f"shade_{h:02d}" for h in HOT_HOURS]
    seg["sun_hot"] = (1 - seg[hot_cols].mean(axis=1)).round(3)
    seg["exposure"] = seg["demand"] * seg["sun_hot"]          # trips walked in the sun
    seg["score"] = 0
    pos = seg["exposure"] > 0
    seg.loc[pos, "score"] = (seg.loc[pos, "exposure"].rank(pct=True) * 100).round().astype(int)
    return seg


def slugify(name):
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug or "street-" + hashlib.md5(name.encode("utf-8")).hexdigest()[:8]


def label_unnamed(seg, pois):
    """Unnamed paths get a useful label from the most important named place within
    MAX_SNAP_M, such as 'Path near Rajiv Chowk Gate 3'."""
    labels = seg["name"].copy()
    named = pois[pois["name"] != ""]
    unnamed = seg["name"] == ""
    if len(named) == 0 or not unnamed.any():
        return labels
    px, py = named.geometry.x.to_numpy(), named.geometry.y.to_numpy()
    weight, names = named["weight"].to_numpy(), named["name"].to_numpy()
    for idx, mid in seg.loc[unnamed].geometry.interpolate(0.5, normalized=True).items():
        d2 = (px - mid.x) ** 2 + (py - mid.y) ** 2
        near = np.flatnonzero(d2 <= MAX_SNAP_M ** 2)
        if len(near):
            best = near[np.lexsort((d2[near], -weight[near]))[0]]   # heaviest place, then nearest
            labels[idx] = f"Path near {names[best]}"
    return labels


def busiest_stretch(g, metres=STRETCH_M):
    """Mean sun-exposed walking on the street's busiest stretch (its best spot to plant)."""
    g = g.sort_values("exposure", ascending=False)
    take = (g["length"].cumsum() - g["length"]) < metres    # segments until 'metres' are covered
    top = g[take]
    return float(np.average(top["exposure"], weights=top["length"]))


def rank_streets(seg, pois):
    """Group segments into streets and rank them by sun-exposed walking on their busiest stretch.
    Benefit per new tree = walkers passing x share of the spot in sun, so the stretch where
    that is highest is where each tree helps most."""
    seg = seg.copy()
    seg["label"] = label_unnamed(seg, pois)
    seg["street_id"] = [slugify(n) if n else f"seg-{i}" for n, i in zip(seg["label"], seg["seg_id"])]
    shade_cols = [f"shade_{h:02d}" for h in HOURS]
    rows = []
    for sid, g in seg.groupby("street_id", sort=False):
        w = g["length"].to_numpy(dtype=float)
        if w.sum() <= 0:
            continue
        rows.append({
            "id": sid,
            "name": g["label"].iloc[0] or "Unnamed path",
            "length": round(float(w.sum()), 1),
            "demand": float(np.average(g["demand"], weights=w)),
            "shade": [round(float(np.average(g[c], weights=w)), 3) for c in shade_cols],
            "sun_hot": round(float(np.average(g["sun_hot"], weights=w)), 3),
            "exposure": busiest_stretch(g) if g["exposure"].sum() > 0 else 0.0,
            "segments": g["seg_id"].astype(int).tolist(),
        })
    streets = pd.DataFrame(rows)
    rankable = (streets["length"] >= MIN_RANK_LENGTH_M) & (streets["exposure"] > 0)
    streets["score"] = 0
    streets.loc[rankable, "score"] = (
        streets.loc[rankable, "exposure"].rank(pct=True) * 100).round().astype(int)
    order = streets[rankable].sort_values("exposure", ascending=False).index
    streets["rank"] = 0
    streets.loc[order, "rank"] = range(1, len(order) + 1)
    return seg, streets


def area_stats(seg, n_trips=SIMULATED_TRIPS):
    """The headline numbers for the planner's stat cards."""
    pos = seg["demand"] > 0
    busy = seg.iloc[0:0]
    if pos.any():
        busy = seg[seg["demand"] >= seg.loc[pos, "demand"].quantile(BUSY_QUANTILE)]
    low = busy[busy["shade_14"] < 0.2]
    share = float(low["length"].sum() / busy["length"].sum()) if len(busy) else 0.0
    return {
        "segments": int(len(seg)),
        "simulated_trips": int(n_trips),
        "busy_low_shade_share": round(share, 3),
    }


def name_metro_gates(pois):
    """Give unnamed metro gates a useful name, such as 'Rajiv Chowk Gate 7'."""
    pois = pois.copy()
    stations = pois[(pois["kind"] == "metro") & (pois["name"] != "")]
    gates = pois.index[(pois["kind"] == "metro") & (pois["name"] == "") & (pois["ref"] != "")]
    for i in gates:
        if len(stations):
            d = stations.geometry.distance(pois.geometry[i])
            if d.min() <= 400:
                pois.at[i, "name"] = f"{stations.loc[d.idxmin(), 'name']} Gate {pois.at[i, 'ref']}"
                continue
        pois.at[i, "name"] = f"Metro gate {pois.at[i, 'ref']}"
    return pois
