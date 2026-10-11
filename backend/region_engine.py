"""Shade Routes for the big offline region (data/region/region.db, made by build_region.py).

Works with no internet. Routing uses the same rules as engine.py (shady route at most
30 % longer, the same penalties), but the shade comes from the week-by-hour table, so
any date and time of the year works, not just one day.
"""
import datetime as dt
import json
import math
import os
import sqlite3
import threading
import zlib
from array import array
from functools import lru_cache

import networkx as nx

from engine import MAX_DETOUR, MAX_SNAP_M, SHADY_PENALTIES, WALK_M_PER_MIN, hour_label

HERE = os.path.dirname(os.path.abspath(__file__))
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
MAX_ROUTE_KM = float(os.environ.get("REGION_MAX_ROUTE_KM", "15"))   # longer walks would load too much of the city
MAX_FEATURES = int(os.environ.get("REGION_MAX_FEATURES", "25000"))             # most street pieces the map asks for at once

# Walking routes never use these roads; lanes tagged private are allowed with a small penalty
NO_WALK = ("motorway", "construction", "proposed", "raceway", "bus_guideway", "planned", "abandoned")
PRIVATE_PENALTY = 1.25

# Planner assumptions (the same as engine.py's planting simulator)
TREE_SPACING_M, TREE_SHADE_M2, CORRIDOR_WIDTH_M = 8, 40, 8
TARGET_SHADE = 0.6                # plan enough trees to bring a street to 60 % shade
# How busy a street type usually is with people on foot (no counts exist for all of NCR)
FOOTFALL = {"pedestrian": 1.0, "primary": 1.0, "secondary": 1.0, "tertiary": 0.9, "living_street": 0.85,
            "footway": 0.8, "residential": 0.7, "unclassified": 0.6, "trunk": 0.6, "steps": 0.5,
            "path": 0.5, "service": 0.4, "cycleway": 0.3, "track": 0.3}

# The app's category chips, as the place classes saved by build_region.py
CAT_CLASSES = {
    "park": {"park", "garden", "playground", "nature_reserve", "sports_centre", "stadium", "swimming_pool"},
    "cafe": {"cafe", "bakery", "ice_cream"},
    "food": {"restaurant", "fast_food", "food_court", "bar", "pub", "biergarten"},
    "transit": {"metro", "station"},
    "shop": {"shop", "marketplace", "mall", "supermarket", "department_store"},
    "sights": {"place_of_worship", "attraction", "museum", "monument", "memorial", "fort", "castle", "ruins", "tomb",
               "archaeological_site", "city_gate", "viewpoint", "art_gallery", "theatre", "cinema", "zoo"},
    "health": {"hospital", "clinic", "pharmacy", "doctors", "dentist"},
}


def _db_path():
    """Local file, or on AWS Lambda the copy in S3 (REGION_DB_S3=s3://bucket/key), downloaded once to /tmp."""
    s3 = os.environ.get("REGION_DB_S3", "")
    if s3.startswith("s3://"):
        local = os.path.join(os.environ.get("REGION_TMP", "/tmp"), "region.db")
        if not os.path.exists(local):
            import boto3                                          # always available on Lambda
            bucket, key = s3[5:].split("/", 1)
            boto3.client("s3").download_file(bucket, key, local + ".part")
            os.replace(local + ".part", local)
        return local
    for p in (os.environ.get("REGION_DB"), os.path.join(HERE, "data", "region", "region.db"),
              os.path.join(HERE, "..", "data", "region", "region.db")):
        if p and os.path.exists(p):
            return p
    return None


def parse_when(when):
    """'2026-05-15T14:30' (Delhi time unless it says otherwise), a datetime, or None for now."""
    if when is None or when == "":
        return dt.datetime.now(IST)
    if isinstance(when, str):
        when = dt.datetime.fromisoformat(when.replace("Z", "+00:00"))
    return when.replace(tzinfo=IST) if when.tzinfo is None else when.astimezone(IST)


def solar_elevation(when, lat, lon):
    """Sun height in degrees (NOAA formula, good to about 0.5 degrees)."""
    utc = when.astimezone(dt.timezone.utc)
    doy = utc.timetuple().tm_yday
    hour = utc.hour + utc.minute / 60 + utc.second / 3600
    g = 2 * math.pi / 365 * (doy - 1 + (hour - 12) / 24)
    decl = (0.006918 - 0.399912 * math.cos(g) + 0.070257 * math.sin(g) - 0.006758 * math.cos(2 * g)
            + 0.000907 * math.sin(2 * g) - 0.002697 * math.cos(3 * g) + 0.00148 * math.sin(3 * g))
    eqt = 229.18 * (0.000075 + 0.001868 * math.cos(g) - 0.032077 * math.sin(g)
                    - 0.014615 * math.cos(2 * g) - 0.040849 * math.sin(2 * g))
    ha = math.radians((hour * 60 + eqt + 4 * lon) / 4 - 180)
    la = math.radians(lat)
    cz = math.sin(la) * math.sin(decl) + math.cos(la) * math.cos(decl) * math.cos(ha)
    return 90 - math.degrees(math.acos(max(-1.0, min(1.0, cz))))


# ---------- names for unnamed paths: type + Plus Code (Open Location Code) ----------
_OLC = "23456789CFGHJMPQRVWX"
KIND = {"footway": "Footpath", "path": "Footpath", "pedestrian": "Footpath", "corridor": "Footpath", "bridleway": "Footpath",
        "steps": "Steps", "service": "Service lane", "residential": "Lane", "living_street": "Lane", "unclassified": "Lane",
        "road": "Lane", "track": "Track", "cycleway": "Cycle path"}


def plus_code(lat, lon):
    """10-digit Plus Code of a point (about 14 m x 14 m), e.g. 7JWVG5FJ+2V. Google Maps understands these."""
    la = int((min(max(lat, -90.0), 89.9999999) + 90.0) * 8000)
    lo = int(((lon + 180.0) % 360.0) * 8000)
    pairs = []
    for _ in range(5):
        pairs.append(_OLC[la % 20] + _OLC[lo % 20])
        la //= 20
        lo //= 20
    code = "".join(reversed(pairs))
    return code[:8] + "+" + code[8:]


def path_id(hw, lat, lon):
    """A stable ID for a path with no name: its type and the short Plus Code of its middle, e.g. 'Lane G5FJ+2V'.
    The short code (last 6 characters) is unique within about 50 km, so across all of Delhi NCR."""
    code = plus_code(lat, lon)[4:]
    return KIND.get(hw, "Path"), code


def _mid(c):
    return c[len(c) // 2]


def _coords(blob):
    a = array("i")
    a.frombytes(blob)
    return [[a[i] / 1e6, a[i + 1] / 1e6] for i in range(0, len(a), 2)]


def _dist_m(lat1, lon1, lat2, lon2):
    k = math.cos(math.radians((lat1 + lat2) / 2))
    return math.hypot((lon2 - lon1) * k * 111_320, (lat2 - lat1) * 111_320)


class Region:
    def __init__(self, path):
        self.path = path
        # read-only and immutable: no locks, safe on Lambda and while the laptop serves it
        self.db = sqlite3.connect(f"file:{os.path.abspath(path)}?mode=ro&immutable=1", uri=True, check_same_thread=False)
        self.lock = threading.Lock()
        self.meta = {k: json.loads(v) for k, v in self.db.execute("SELECT k, v FROM meta")}
        self.hours, self.weeks = self.meta["hours"], self.meta["weeks"]
        self.scale, self.G = self.meta["scale"], self.meta["grid_deg"]
        self.lat, self.lon = self.meta["center"]
        self.has_walk = any(c[1] == "walk" for c in self.db.execute("PRAGMA table_info(segs)"))

    def _q(self, sql, args=()):
        with self.lock:
            return self.db.execute(sql, args).fetchall()

    # ---------- time ----------
    def slot(self, when):
        """Where a moment falls in the week x hour table, and whether the sun is down."""
        t = parse_when(when)
        doy = t.timetuple().tm_yday
        wf = min(max((doy - 4) / 7, 0), self.weeks - 1)
        hf = min(max(t.hour + t.minute / 60, self.hours[0]), self.hours[-1]) - self.hours[0]
        night = solar_elevation(t, self.lat, self.lon) <= 0
        return {"t": t, "wf": wf, "hf": hf, "night": night}

    def shade_value(self, blob, s):
        """Shade (0..1) of one street piece at a slot, blending the nearest weeks and hours."""
        if s["night"]:
            return 1.0
        b = zlib.decompress(blob)
        H = len(self.hours)
        w0, h0 = int(s["wf"]), int(s["hf"])
        w1, h1 = min(w0 + 1, self.weeks - 1), min(h0 + 1, H - 1)
        fw, fh = s["wf"] - w0, s["hf"] - h0
        v = ((b[w0 * H + h0] * (1 - fh) + b[w0 * H + h1] * fh) * (1 - fw)
             + (b[w1 * H + h0] * (1 - fh) + b[w1 * H + h1] * fh) * fw)
        return v / self.scale

    # ---------- what the map shows ----------
    def info(self):
        m = {k: v for k, v in self.meta.items() if k not in ("average_shade", "week_days")}
        basemap = os.path.exists(os.path.join(os.path.dirname(self.path), "basemap.mbtiles"))
        return {**m, "offline": True, "basemap": basemap}

    def _segments(self, west, south, east, north, cols="id, u, v, len, name, highway, coords, shade", walk_only=False):
        G = self.G
        extra = " AND walk = 1" if walk_only and self.has_walk else ""
        return self._q(f"SELECT {cols} FROM segs WHERE gx BETWEEN ? AND ? AND gy BETWEEN ? AND ?{extra}",
                       (math.floor(west / G) - 1, math.floor(east / G) + 1,
                        math.floor(south / G) - 1, math.floor(north / G) + 1))

    def shade_geojson(self, west, south, east, north, when=None):
        s = self.slot(when)
        feats = []
        for sid, _, _, length, name, hw, cblob, sblob in self._segments(west, south, east, north):
            c = _coords(cblob)
            xs, ys = [p[0] for p in c], [p[1] for p in c]
            if max(xs) < west or min(xs) > east or max(ys) < south or min(ys) > north:
                continue
            feats.append({"type": "Feature",          # kept small: AWS Lambda answers are limited to 6 MB
                          "geometry": {"type": "LineString", "coordinates": [[round(x, 5), round(y, 5)] for x, y in c]},
                          "properties": {"id": sid, "shade": round(self.shade_value(sblob, s), 2)}})
            if len(feats) >= MAX_FEATURES:
                break
        return {"type": "FeatureCollection", "features": feats,
                "when": s["t"].isoformat(timespec="minutes"), "night": s["night"],
                "truncated": len(feats) >= MAX_FEATURES}

    def street_day(self, seg_id, date=None):
        """Shade of one street piece for every hour of one day."""
        row = self._q("SELECT name, len, shade, highway, coords FROM segs WHERE id = ?", (seg_id,))
        if not row:
            raise KeyError(seg_id)
        name, length, blob, hw, cblob = row[0]
        code = None
        if not name:
            x, y = _mid(_coords(cblob))
            kind, code = path_id(hw, y, x)
            name = f"{kind} {code}"
        day = parse_when(date).date() if date else dt.datetime.now(IST).date()
        out = []
        for h in self.hours:
            t = dt.datetime(day.year, day.month, day.day, h, tzinfo=IST)
            out.append(round(self.shade_value(blob, self.slot(t)), 3))
        return {"id": seg_id, "name": name, "code": code, "length_m": round(length),
                "date": day.isoformat(), "hours": self.hours, "shade": out}

    # ---------- places (offline search) ----------
    def places(self, q, lat=None, lon=None, limit=10):
        q = (q or "").strip()
        if not q:
            return []
        like = f"%{q}%"
        rows = self._q("SELECT name, name_en, cls, lon, lat FROM places WHERE name LIKE ? OR name_en LIKE ? LIMIT 400",
                       (like, like))
        ql = q.lower()

        def rank(r):
            starts = r[0].lower().startswith(ql) or (r[1] or "").lower().startswith(ql)
            d = _dist_m(lat, lon, r[4], r[3]) if lat is not None and lon is not None else 0
            return (not starts, d)
        rows.sort(key=rank)
        return [{"name": r[1] if r[1] and not r[0].isascii() else r[0],     # English name for Hindi-only labels
                 "local_name": r[0], "class": r[2], "lon": r[3], "lat": r[4]} for r in rows[:limit]]

    # ---------- everything of one kind, nearest first, across the whole saved area ----------
    @lru_cache(maxsize=16)
    def _places_of(self, cat):
        classes = sorted(CAT_CLASSES[cat])
        return self._q(f"SELECT name, name_en, cls, lon, lat FROM places WHERE cls IN ({','.join('?' * len(classes))})",
                       classes)

    def nearby(self, lat, lon, cats, limit=50, offset=0):
        out = {}
        for cat in cats:
            if cat not in CAT_CLASSES:
                continue
            rows = sorted(((_dist_m(lat, lon, r[4], r[3]), r) for r in self._places_of(cat)), key=lambda x: x[0])
            out[cat] = {"total": len(rows), "items": [
                {"name": r[1] if r[1] and not r[0].isascii() else r[0], "class": r[2], "lon": r[3], "lat": r[4],
                 "dist_m": round(d)} for d, r in rows[offset:offset + limit]]}
        return out

    # ---------- planner: where new trees would help walkers most, in the area on screen ----------
    def planner(self, west, south, east, north, when=None, limit=15):
        """Streets in the box ranked by how much walking street is in the sun at that time.
        A street's weight stands in for how many people walk it (main roads and markets > lanes > tracks)."""
        s = self.slot(when)
        segs = []
        for sid, _, _, length, name, hw, cblob, sblob in self._segments(west, south, east, north, walk_only=True):
            c = _coords(cblob)
            mx, my = _mid(c)
            if west <= mx <= east and south <= my <= north:
                segs.append((sid, length, name, hw, c, sblob))
        # unnamed pieces of the same kind that simply continue each other (a bend, not a junction)
        # count as one path, so a long footpath gets one ID instead of one per piece
        parent = {}

        def find(a):
            while parent.get(a, a) != a:
                parent[a] = parent.get(parent[a], parent[a])
                a = parent[a]
            return a
        at = {}
        for sid, length, name, hw, c, _ in segs:
            for p in (c[0], c[-1]):
                at.setdefault((round(p[0], 5), round(p[1], 5)), []).append((sid, name, KIND.get(hw, "Path")))
        for pieces in at.values():
            if len(pieces) == 2 and not pieces[0][1] and not pieces[1][1] and pieces[0][2] == pieces[1][2] \
                    and pieces[0][0] != pieces[1][0]:
                parent[find(pieces[0][0])] = find(pieces[1][0])
        longest = {}
        for sid, length, name, hw, c, _ in segs:
            if not name:
                r = find(sid)
                if r not in longest or length > longest[r][0]:
                    longest[r] = (length, hw, c)
        streets = {}
        for sid, length, name, hw, c, sblob in segs:
            if name:
                key, label, code = name, name, None
            else:
                key = f"#{find(sid)}"
                _, lhw, lc = longest[find(sid)]
                x, y = _mid(lc)
                kind, code = path_id(lhw, y, x)
                label = f"{kind} {code}"
            st = streets.setdefault(key, {"name": label, "code": code, "len": 0.0, "shaded": 0.0,
                                          "hw": {}, "lines": [], "w": 0.0})
            sh = self.shade_value(sblob, s)
            st["len"] += length
            st["shaded"] += length * sh
            st["hw"][hw] = st["hw"].get(hw, 0) + length
            st["w"] += length * FOOTFALL.get(hw, 0.5)
            st["lines"].append([[round(x, 5), round(y, 5)] for x, y in c])
        rows, total_len, total_shaded, sunny_len = [], 0.0, 0.0, 0.0
        for st in streets.values():
            total_len += st["len"]
            total_shaded += st["shaded"]
            shade = st["shaded"] / st["len"] if st["len"] else 0
            if shade < 0.2:
                sunny_len += st["len"]
            if st["len"] < 60 or (st["code"] and st["len"] < 120):
                continue                                          # too short to plan a row of trees
            hw = max(st["hw"], key=st["hw"].get)
            sun_m = st["len"] * (1 - shade)
            need = max(0.0, TARGET_SHADE - shade) * st["len"] * CORRIDOR_WIDTH_M / TREE_SHADE_M2
            trees = min(int(math.ceil(need)), int(2 * st["len"] / TREE_SPACING_M))
            after = min(1.0, shade + trees * TREE_SHADE_M2 / (st["len"] * CORRIDOR_WIDTH_M))
            rows.append({"name": st["name"], "code": st["code"], "highway": hw, "length_m": round(st["len"]),
                         "shade_pct": round(100 * shade), "sun_m": round(sun_m),
                         "trees": trees, "shade_after_pct": round(100 * after),
                         "raw": st["w"] / st["len"] * sun_m, "lines": st["lines"]})
        rows.sort(key=lambda r: -r["raw"])
        top = rows[0]["raw"] if rows else 1
        for r in rows:
            r["score"] = max(1, round(100 * r.pop("raw") / top))
        return {"when": s["t"].isoformat(timespec="minutes"), "night": s["night"],
                "streets": len(streets), "km": round(total_len / 1000, 1),
                "avg_shade_pct": round(100 * total_shaded / total_len) if total_len else 0,
                "sunny_pct": round(100 * sunny_len / total_len) if total_len else 0,
                "rows": rows[:limit]}

    def shade_at(self, lat, lon, when=None):
        """The street nearest a point: its shade now and through that day."""
        m = 150 / 111_320
        k = math.cos(math.radians(lat))
        best, best_d = None, 1e9
        for sid, _, _, length, name, hw, cblob, sblob in self._segments(lon - m / k, lat - m, lon + m / k, lat + m):
            for x, y in _coords(cblob):
                d = _dist_m(lat, lon, y, x)
                if d < best_d:
                    best, best_d = (sid, name, sblob, hw, cblob), d
        if best is None or best_d > 300:
            return None
        s = self.slot(when)
        day = self.street_day(best[0], s["t"].isoformat())
        hrs = [(h, v) for h, v in zip(day["hours"], day["shade"])]
        sunny_hours = [h for h, v in hrs if 9 <= h <= 17]
        best_hour = max(sunny_hours, key=lambda h: dict(hrs)[h]) if sunny_hours else None
        return {"street": day["name"], "distance_m": round(best_d),
                "shade_now_pct": round(100 * self.shade_value(best[2], s)), "night": s["night"],
                "when": s["t"].isoformat(timespec="minutes"),
                "by_hour_pct": {f"{h}:00": round(100 * v) for h, v in hrs},
                "shadiest_daytime_hour": f"{best_hour}:00" if best_hour is not None else None}

    # ---------- shady routing (same rules as engine.Area.route) ----------
    def route(self, from_lat, from_lon, to_lat, to_lon, when=None):
        d = _dist_m(from_lat, from_lon, to_lat, to_lon)
        if d > MAX_ROUTE_KM * 1000:
            raise ValueError(f"That is {d / 1000:.0f} km. Walking routes go up to {MAX_ROUTE_KM:g} km.")
        s = self.slot(when)
        m = max(700.0, 0.35 * d) / 111_320
        k = math.cos(math.radians(from_lat))
        west, east = min(from_lon, to_lon) - m / k, max(from_lon, to_lon) + m / k
        south, north = min(from_lat, to_lat) - m, max(from_lat, to_lat) + m
        G, segs, ends = nx.Graph(), {}, {}
        cols = "id, u, v, len, name, highway, coords, shade" + (", walk" if self.has_walk else ", 1")
        for sid, u, v, length, name, hw, cblob, sblob, walk in self._segments(west, south, east, north, cols=cols):
            if u == v or any(x in (hw or "") for x in NO_WALK):
                continue
            # lanes tagged private (gated colonies) or cut off from the main network are still walkable in
            # practice, so routes may use them, but a little reluctantly
            w = length * (1.0 if walk else PRIVATE_PENALTY)
            if G.has_edge(u, v) and G[u][v]["w"] <= w:
                continue
            c = _coords(cblob)
            segs[sid] = {"u": u, "coords": c, "len": length, "name": name,
                         "shade": self.shade_value(sblob, s)}
            ends[u], ends[v] = c[0], c[-1]
            G.add_edge(u, v, length=length, w=w, seg=sid)
        if not G:
            raise ValueError("There are no streets here in the offline data.")

        def snap(lat, lon):
            best = min(ends, key=lambda n: (ends[n][0] - lon) ** 2 * k * k + (ends[n][1] - lat) ** 2)
            return best, _dist_m(lat, lon, ends[best][1], ends[best][0])
        a, da = snap(from_lat, from_lon)
        b, db = snap(to_lat, to_lon)
        if max(da, db) > MAX_SNAP_M:
            raise ValueError("Pick points inside the saved area, close to a street.")
        if a == b:
            raise ValueError("Start and end are the same place. Pick two different points.")

        def stats(path):
            coords, length, shaded = [], 0.0, 0.0
            for p, q in zip(path[:-1], path[1:]):
                seg = segs[G[p][q]["seg"]]
                pts = seg["coords"] if seg["u"] == p else seg["coords"][::-1]
                coords.extend(pts if not coords else pts[1:])
                length += seg["len"]
                shaded += seg["len"] * seg["shade"]
            return {"length_m": round(length), "minutes": max(1, round(length / WALK_M_PER_MIN)),
                    "shade_pct": round(100 * shaded / length) if length else 0,
                    "sun_m": round(length - shaded), "coords": coords}
        try:
            fastest = stats(nx.shortest_path(G, a, b, weight="w"))
        except nx.NetworkXNoPath:
            raise ValueError("No walking route connects these two points.")
        shadiest = fastest
        for pen in SHADY_PENALTIES:
            def cost(u, v, e, pen=pen):
                return e["w"] * (1 + pen * (1 - segs[e["seg"]]["shade"]))
            cand = stats(nx.shortest_path(G, a, b, weight=cost))
            if cand["length_m"] <= fastest["length_m"] * MAX_DETOUR:
                shadiest = cand
                break
        extra = shadiest["minutes"] - fastest["minutes"]
        gain = shadiest["shade_pct"] - fastest["shade_pct"]
        if s["night"]:
            summary = "The sun is down, so every route is in shade. This is the fastest one."
            shadiest = fastest
        elif gain <= 0:
            summary = "The fastest route is already the shadiest one."
        elif extra <= 0:
            summary = f"Same time, {gain} percentage points more shade."
        else:
            summary = (f"{extra} minute{'s' if extra != 1 else ''} longer, "
                       f"{shadiest['shade_pct']}% in shade instead of {fastest['shade_pct']}%.")
        hour = s["t"].hour
        return {"when": s["t"].isoformat(timespec="minutes"), "hour": hour, "hour_label": hour_label(hour),
                "night": s["night"], "fastest": fastest, "shadiest": shadiest, "summary": summary}


@lru_cache(maxsize=1)
def get_region():
    """The offline region, or None if build_region.py has not been run yet."""
    path = _db_path()
    return Region(path) if path else None
