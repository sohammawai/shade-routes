"""The Shade Routes engine (Phase 7).

Pure Python plus networkx: everything heavy was precomputed in Phases 5 and 6,
so this file runs on a laptop or inside AWS Lambda without any map libraries.

Data comes from data/web/ on your laptop, or from S3 when DATA_BUCKET is set.
"""
import difflib
import json
import math
import os
from functools import lru_cache

import networkx as nx

HERE = os.path.dirname(os.path.abspath(__file__))
_BUNDLED = os.path.join(HERE, "data", "web")                  # data packed into the Lambda zip
DATA_DIR = os.environ.get("DATA_DIR") or (
    _BUNDLED if os.path.isdir(_BUNDLED) else os.path.join(HERE, "..", "data", "web"))
DATA_BUCKET = os.environ.get("DATA_BUCKET", "")              # on AWS, e.g. "shade-routes-data-soham"
DATA_PREFIX = os.environ.get("DATA_PREFIX", "web/")          # folder inside the bucket

WALK_M_PER_MIN = 78                  # walking speed, about 4.7 km/h
SHADY_PENALTIES = [3.0, 2.0, 1.0, 0.5]   # how hard the shady route avoids sun, strongest first
MAX_DETOUR = 1.30                    # the shady route may be at most 30% longer
MAX_SNAP_M = 250                     # a chosen point must be this close to a street

# Planting simulator assumptions (say them out loud in the pitch)
TREE_SPACING_M = 8                   # at most one new tree every 8 m on each side
TREE_SHADE_M2 = 40                   # walking-corridor area one new tree shades
CORRIDOR_WIDTH_M = 8                 # the same 8 m corridor as Phase 5


def _read_json(name):
    if DATA_BUCKET:
        import boto3                                          # only needed on AWS
        obj = boto3.client("s3").get_object(Bucket=DATA_BUCKET, Key=DATA_PREFIX + name)
        return json.loads(obj["Body"].read())
    with open(os.path.join(DATA_DIR, name), encoding="utf-8") as f:
        return json.load(f)


@lru_cache(maxsize=1)
def list_areas():
    return _read_json("areas_index.json")["areas"]


@lru_cache(maxsize=8)
def get_area(key):
    if key not in {a["key"] for a in list_areas()}:
        raise KeyError(key)
    return Area(_read_json(f"{key}.json"))


def hour_label(h):
    return f"{h % 12 or 12} {'AM' if h < 12 else 'PM'}"


class Area:
    def __init__(self, data):
        self.data = data
        self.key, self.name = data["key"], data["name"]
        self.hours = data["hours"]
        self.nodes = {n[0]: (n[1], n[2]) for n in data["nodes"]}       # id -> (lon, lat)
        self.segments = {s["id"]: s for s in data["segments"]}
        self.streets = {s["id"]: s for s in data["streets"]}
        self.places = data.get("places", [])
        self.G = nx.Graph()
        for s in data["segments"]:
            u, v = s["u"], s["v"]
            if u == v or (self.G.has_edge(u, v) and self.G[u][v]["length"] <= s["len"]):
                continue
            self.G.add_edge(u, v, length=s["len"], seg=s["id"])

    # ---------- helpers ----------
    def hour_index(self, hour):
        hour = min(max(int(round(hour)), self.hours[0]), self.hours[-1])
        return self.hours.index(hour)

    def nearest_node(self, lat, lon):
        """Closest intersection to a point, and the distance in metres."""
        coslat = math.cos(math.radians(lat))
        best, best_d2 = None, float("inf")
        for nid, (nlon, nlat) in self.nodes.items():
            if nid not in self.G:
                continue
            dx = (nlon - lon) * coslat * 111_320
            dy = (nlat - lat) * 111_320
            d2 = dx * dx + dy * dy
            if d2 < best_d2:
                best, best_d2 = nid, d2
        return best, math.sqrt(best_d2)

    def street_bounds(self, street):
        pts = [c for sid in street["segments"] for c in self.segments[sid]["coords"]]
        lons, lats = [p[0] for p in pts], [p[1] for p in pts]
        return [[min(lats), min(lons)], [max(lats), max(lons)]]   # Leaflet order

    def find_street(self, name):
        """A street by id or by (roughly) its name."""
        if name in self.streets:
            return self.streets[name]
        by_name = {s["name"].lower(): s for s in self.streets.values() if s["name"] != "Unnamed path"}
        q = name.lower().strip()
        if q in by_name:
            return by_name[q]
        for n, s in by_name.items():
            if q in n:
                return s
        close = difflib.get_close_matches(q, list(by_name), n=1, cutoff=0.6)
        return by_name[close[0]] if close else None

    def find_places(self, query, limit=8):
        q = query.lower().strip()
        hits = [p for p in self.places if q in p["name"].lower()] if q else list(self.places)
        if not hits and q:
            names = [p["name"].lower() for p in self.places]
            close = set(difflib.get_close_matches(q, names, n=limit, cutoff=0.5))
            hits = [p for p in self.places if p["name"].lower() in close]
        return hits[:limit]

    # ---------- what the website shows ----------
    def info(self):
        d = self.data
        return {"key": self.key, "name": self.name, "center": d["center"], "radius": d["radius"],
                "date": d["date"], "hours": self.hours, "hot_hours": d["hot_hours"], "stats": d["stats"]}

    def streets_geojson(self):
        features = [{
            "type": "Feature",
            "geometry": {"type": "LineString", "coordinates": s["coords"]},
            "properties": {"id": s["id"], "street": s["street"], "name": s["name"],
                           "score": s["score"], "demand": s["demand"], "shade": s["shade"]},
        } for s in self.data["segments"]]
        return {"type": "FeatureCollection", "features": features}

    def street_summary(self, s, hi=None):
        hi = self.hour_index(14) if hi is None else hi
        return {"id": s["id"], "name": s["name"], "rank": s["rank"], "score": s["score"],
                "length_m": round(s["length"]), "trips_per_day": s["demand"],
                "shade_pct": round(100 * s["shade"][hi]), "hour": self.hours[hi]}

    def top_streets(self, limit=10, hour=14):
        hi = self.hour_index(hour)
        ranked = sorted((s for s in self.streets.values() if s["rank"]), key=lambda s: s["rank"])
        return [self.street_summary(s, hi) for s in ranked[:limit]]

    def street_detail(self, street_id):
        s = self.streets[street_id]
        return {**self.street_summary(s), "shade": s["shade"], "hours": self.hours,
                "sun_hot_pct": round(100 * s["sun_hot"]), "segments": s["segments"],
                "bounds": self.street_bounds(s), "max_trees": self.max_trees(s)}

    # ---------- planting simulator ----------
    def max_trees(self, s):
        return max(1, int(2 * s["length"] / TREE_SPACING_M))

    def simulate(self, street_id, trees, hour=14):
        s = self.streets[street_id]
        n = max(0, min(int(trees), self.max_trees(s)))
        added = min(1.0, n * TREE_SHADE_M2 / (s["length"] * CORRIDOR_WIDTH_M))
        after = [round(x + (1 - x) * added, 3) for x in s["shade"]]
        hi = self.hour_index(hour)
        # demand x length = metres walked on this street per day; trips spread over the day
        gain = sum(a - b for a, b in zip(after, s["shade"])) / len(after)
        return {
            "street_id": street_id, "name": s["name"], "trees": n, "max_trees": self.max_trees(s),
            "hour": self.hours[hi], "hours": self.hours,
            "before": s["shade"], "after": after,
            "shade_before_pct": round(100 * s["shade"][hi]),
            "shade_after_pct": round(100 * after[hi]),
            "walkers_per_day": s["demand"],
            "shaded_km_per_day": round(s["demand"] * s["length"] * gain / 1000, 1),
            "assumptions": f"Each new tree shades about {TREE_SHADE_M2} m² of an {CORRIDOR_WIDTH_M} m "
                           f"walking corridor; at most one tree every {TREE_SPACING_M} m on each side.",
        }

    # ---------- shady routing ----------
    def _route_stats(self, path, hi):
        coords, length, shaded = [], 0.0, 0.0
        for a, b in zip(path[:-1], path[1:]):
            seg = self.segments[self.G[a][b]["seg"]]
            pts = seg["coords"] if seg["u"] == a else seg["coords"][::-1]
            coords.extend(pts if not coords else pts[1:])
            length += seg["len"]
            shaded += seg["len"] * seg["shade"][hi]
        return {"length_m": round(length), "minutes": max(1, round(length / WALK_M_PER_MIN)),
                "shade_pct": round(100 * shaded / length) if length else 0,
                "sun_m": round(length - shaded), "coords": coords}

    def route(self, from_lat, from_lon, to_lat, to_lon, hour=14):
        hi = self.hour_index(hour)
        a, da = self.nearest_node(from_lat, from_lon)
        b, db = self.nearest_node(to_lat, to_lon)
        if max(da, db) > MAX_SNAP_M:
            raise ValueError(f"Pick points inside {self.name}, close to a street.")
        if a == b:
            raise ValueError("Start and end are the same place. Pick two different points.")
        try:
            fastest = self._route_stats(nx.shortest_path(self.G, a, b, weight="length"), hi)
        except nx.NetworkXNoPath:
            raise ValueError("No walking route connects these two points.")

        shadiest = fastest
        for p in SHADY_PENALTIES:
            def cost(u, v, d, p=p):
                return d["length"] * (1 + p * (1 - self.segments[d["seg"]]["shade"][hi]))
            cand = self._route_stats(nx.shortest_path(self.G, a, b, weight=cost), hi)
            if cand["length_m"] <= fastest["length_m"] * MAX_DETOUR:
                shadiest = cand
                break

        extra = shadiest["minutes"] - fastest["minutes"]
        gain = shadiest["shade_pct"] - fastest["shade_pct"]
        if gain <= 0:
            summary = "The fastest route is already the shadiest one."
        elif extra <= 0:
            summary = f"Same time, {gain} percentage points more shade."
        else:
            summary = (f"{extra} minute{'s' if extra != 1 else ''} longer, "
                       f"{shadiest['shade_pct']}% in shade instead of {fastest['shade_pct']}%.")
        return {"hour": self.hours[hi], "hour_label": hour_label(self.hours[hi]),
                "fastest": fastest, "shadiest": shadiest, "summary": summary}


def compare_areas():
    """Shade through the day for every area, for the comparison chart."""
    return [{"key": a["key"], "name": a["name"], "hours": a["hours"],
             "hourly_shade": a["stats"]["hourly_shade"]} for a in list_areas()]
