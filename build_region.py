"""Build shade data for a big circle (default: 40 km around Chhatarpur) that works offline.

It uses the same shade rules as build_area.py and shade_lib.py (8 m corridor, building
heights, 300 m shadow cap, satellite canopy from 3 m, mapped trees as 4 m circles, all
shade after sunset), but for every week of the year and every hour from 6 AM to 7 PM.

Usage (from the shade-routes folder, with your venv active):
    pip install osmium numba requests             (once)
    python build_region.py                        run every step
    python build_region.py --workers 6            use 6 CPU cores for the shade step
    python build_region.py --step shade           run one step only (download, osm, prepare, shade, pack)
    python build_region.py --pbf my_extract.osm.pbf    use an OSM file you already have

You can stop it at any time (Ctrl+C) and run it again: finished work is kept.
Internet is needed only for the first download of OpenStreetMap and the satellite canopy.
The result is one file, data/region/region.db, that the backend reads offline.
"""
import argparse
import json
import math
import os
import pickle
import sqlite3
import sys
import time
import zlib
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait

import numpy as np
from numba import njit

import region_config as C

STEPS = ["download", "osm", "prepare", "shade", "pack"]


def log(msg):
    print(msg, flush=True)


def work(*parts):
    path = os.path.join(C.WORK_DIR, *parts)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    return path


def projector():
    from pyproj import Transformer
    return (Transformer.from_crs("EPSG:4326", C.PROJ_CRS, always_xy=True),
            Transformer.from_crs(C.PROJ_CRS, "EPSG:4326", always_xy=True))


def region_circle(center, radius):
    """The circle in projected metres and in lon/lat."""
    import shapely
    from shapely.geometry import Point
    fwd, inv = projector()
    cx, cy = fwd.transform(center[1], center[0])
    circ = Point(cx, cy).buffer(radius, 64)
    circ_ll = shapely.transform(circ, lambda c: np.column_stack(inv.transform(c[:, 0], c[:, 1])))
    return (cx, cy), circ, circ_ll


# =====================================================================
# 1. Download OpenStreetMap (only the Geofabrik zones that touch the circle)
# =====================================================================
def parse_poly(text):
    from shapely.geometry import Polygon
    from shapely.ops import unary_union
    rings, cur, name = [], None, None
    for line in text.splitlines()[1:]:
        s = line.strip()
        if not s:
            continue
        if cur is None:
            if s == "END":
                break
            name, cur = s, []
        elif s == "END":
            if not name.startswith("!") and len(cur) >= 3:
                rings.append(Polygon(cur))
            cur = None
        else:
            lon, lat = map(float, s.split()[:2])
            cur.append((lon, lat))
    return unary_union(rings)


def download(url, path):
    import requests
    part = path + ".part"
    have = os.path.getsize(part) if os.path.exists(part) else 0
    headers = {"Range": f"bytes={have}-"} if have else {}
    with requests.get(url, headers=headers, stream=True, timeout=60) as r:
        if r.status_code == 416:                       # already complete
            os.replace(part, path)
            return
        r.raise_for_status()
        if have and r.status_code != 206:              # server ignored the resume request
            have = 0
        total = have + int(r.headers.get("Content-Length", 0))
        done, last = have, 0
        with open(part, "ab" if have else "wb") as f:
            for chunk in r.iter_content(1 << 20):
                f.write(chunk)
                done += len(chunk)
                if time.time() - last > 5:
                    last = time.time()
                    log(f"    {done / 1e6:.0f} of {total / 1e6:.0f} MB")
    os.replace(part, path)


def step_download(args):
    if args.pbf:
        pbfs = [os.path.abspath(p) for p in args.pbf]
    else:
        import requests
        os.makedirs(C.OSM_DIR, exist_ok=True)
        _, _, circ_ll = region_circle(args.center, args.radius)
        pbfs = []
        for zone in C.ZONES:
            poly_path = os.path.join(C.OSM_DIR, zone + ".poly")
            if not os.path.exists(poly_path):
                r = requests.get(C.GEOFABRIK + zone + ".poly", timeout=60)
                r.raise_for_status()
                with open(poly_path, "w", encoding="utf-8") as f:
                    f.write(r.text)
            with open(poly_path, encoding="utf-8") as f:
                if not parse_poly(f.read()).intersects(circ_ll):
                    continue
            pbf = os.path.join(C.OSM_DIR, zone + "-latest.osm.pbf")
            if os.path.exists(pbf):
                log(f"  {zone}: already downloaded")
            else:
                log(f"  {zone}: downloading OpenStreetMap (first run only)...")
                download(C.GEOFABRIK + zone + "-latest.osm.pbf", pbf)
            pbfs.append(pbf)
        if not pbfs:
            sys.exit("No Geofabrik zone covers this circle. Download an .osm.pbf yourself and use --pbf.")
    with open(work("pbfs.json"), "w") as f:
        json.dump(pbfs, f)
    log(f"  Using {', '.join(os.path.basename(p) for p in pbfs)}")


# =====================================================================
# 2. Read streets, buildings, trees and places from OpenStreetMap
# =====================================================================
# The same walking filter osmnx uses for network_type="walk" (as in shade_lib.load_streets)
WALK_EXCLUDE = ("abandoned", "bus_guideway", "construction", "cycleway", "motor", "no", "planned",
                "platform", "proposed", "raceway", "razed")
PLACE_KEYS = ("amenity", "shop", "leisure", "tourism", "railway", "public_transport", "historic")
PLACE_LEISURE = {"park", "garden", "playground", "sports_centre", "stadium", "nature_reserve", "swimming_pool"}


# Roads that get shade colours: every road that exists (motorways and roads under construction too).
# Only roads that aren't there (yet or any more) are skipped. Walking routes still follow is_walkable().
ROAD_EXCLUDE = ("abandoned", "planned", "proposed", "razed", "disused", "platform", "bus_stop", "elevator")


def is_road(t):
    hw = t.get("highway")
    return bool(hw) and "yes" not in t.get("area", "")


def road_ok(hw):
    return hw != "no" and not any(x in hw for x in ROAD_EXCLUDE)


def is_walkable(t):
    hw = t.get("highway")
    if not hw or any(x in hw for x in WALK_EXCLUDE):
        return False
    return not ("yes" in t.get("area", "") or "private" in t.get("access", "")
                or "no" in t.get("foot", "") or "private" in t.get("service", ""))


def place_class(t):
    if not t.get("name"):
        return None
    if t.get("railway") in ("station", "halt") or t.get("public_transport") == "station":
        return "metro" if t.get("station") == "subway" or t.get("subway") == "yes" else "station"
    if t.get("leisure") in PLACE_LEISURE:
        return t["leisure"]
    for k in ("amenity", "tourism", "historic"):
        if t.get(k):
            return t[k]
    if t.get("shop"):
        return "shop"
    return None


def first_text(v):
    return (v or "").split(";")[0].strip()


def step_osm(args):
    import osmium
    import shapely
    from shade_lib import _estimate_height

    with open(work("pbfs.json")) as f:
        pbfs = json.load(f)
    _, _, circ_ll = region_circle(args.center, args.radius)
    w0, s0, e0, n0 = circ_ll.bounds
    w0, s0, e0, n0 = w0 - 0.005, s0 - 0.005, e0 + 0.005, n0 + 0.005
    def inside(lon, lat):
        return w0 <= lon <= e0 and s0 <= lat <= n0

    ways, buildings, trees, places = {}, {}, {}, {}
    wkb = osmium.geom.WKBFactory()
    keys = ("highway", "building", "natural", "landuse") + PLACE_KEYS
    for pbf in pbfs:
        log(f"  Reading {os.path.basename(pbf)} (10-30 minutes for a big file)...")
        t0, count = time.time(), 0
        fp = (osmium.FileProcessor(pbf)
              .with_locations()
              .with_areas(osmium.filter.KeyFilter("building", "natural", "landuse", *PLACE_KEYS))
              .with_filter(osmium.filter.KeyFilter(*keys)))
        for o in fp:
            count += 1
            t = dict(o.tags)
            if o.is_node():
                if not o.location.valid():
                    continue
                lon, lat = o.location.lon, o.location.lat
                if not inside(lon, lat):
                    continue
                if t.get("natural") == "tree":
                    trees[("n", o.id)] = ("pt", (lon, lat))
                cls = place_class(t)
                if cls:
                    places[("n", o.id)] = (t["name"], t.get("name:en", ""), cls, lon, lat)
            elif o.is_way():
                if is_road(t) and o.id not in ways:
                    pts = [(n.ref, n.lon, n.lat) for n in o.nodes if n.location.valid()]
                    if len(pts) >= 2 and any(inside(p[1], p[2]) for p in pts):
                        ways[o.id] = (np.array([p[0] for p in pts], np.int64),
                                      np.array([p[1] for p in pts]), np.array([p[2] for p in pts]),
                                      first_text(t.get("name")), first_text(t.get("highway")), is_walkable(t))
                if t.get("natural") == "tree_row" and any(
                        n.location.valid() and inside(n.lon, n.lat) for n in o.nodes):
                    try:
                        trees[("w", o.id)] = ("ln", bytes.fromhex(wkb.create_linestring(o)))
                    except Exception:
                        pass
            elif o.is_area():
                key = ("a", o.id)
                if key in buildings or key in trees:
                    continue
                is_b = "building" in t and t.get("location") != "underground"
                if is_b:
                    try:
                        if float(t.get("layer", 0)) < 0:
                            is_b = False
                    except ValueError:
                        pass
                is_t = t.get("natural") in ("wood", "tree_row") or t.get("landuse") == "forest"
                cls = place_class(t)
                if not (is_b or is_t or cls):
                    continue
                try:
                    first = next(iter(next(iter(o.outer_rings()))))
                    if not inside(first.lon, first.lat):
                        continue
                except Exception:
                    continue
                try:
                    g = bytes.fromhex(wkb.create_multipolygon(o))
                except Exception:
                    continue
                if is_b:
                    buildings[key] = (g, _estimate_height(t))
                elif is_t:
                    trees[key] = ("pg", g)
                if cls and key not in places:
                    c = shapely.from_wkb(g).centroid
                    places[key] = (t["name"], t.get("name:en", ""), cls, c.x, c.y)
        log(f"    {count:,} objects in {time.time() - t0:.0f} s")

    log(f"  {len(ways):,} roads and paths, {len(buildings):,} buildings, "
        f"{len(trees):,} mapped trees, {len(places):,} named places")
    with open(work("osm.pkl"), "wb") as f:
        pickle.dump({"ways": ways, "buildings": list(buildings.values()),
                     "trees": list(trees.values()), "places": list(places.values())}, f, protocol=5)


# =====================================================================
# 3. Make the street network and cut the circle into tiles
# =====================================================================
@njit(cache=True)
def _count_samples(x, y, ep, eq, spacing):
    out = np.empty(ep.shape[0], np.int64)
    for e in range(ep.shape[0]):
        L = 0.0
        for k in range(ep[e], eq[e]):
            L += math.hypot(x[k + 1] - x[k], y[k + 1] - y[k])
        out[e] = max(1, int(round(L / spacing)))
    return out


@njit(cache=True)
def _sample(x, y, ep, eq, counts, offsets):
    total = counts.sum() * offsets.shape[0]
    px = np.empty(total)
    py = np.empty(total)
    pe = np.empty(total, np.int64)
    j = 0
    for e in range(ep.shape[0]):
        L = 0.0
        for k in range(ep[e], eq[e]):
            L += math.hypot(x[k + 1] - x[k], y[k + 1] - y[k])
        n = counts[e]
        k, acc = ep[e], 0.0
        for i in range(n):
            target = (i + 0.5) * L / n
            while k < eq[e] - 1 and acc + math.hypot(x[k + 1] - x[k], y[k + 1] - y[k]) < target:
                acc += math.hypot(x[k + 1] - x[k], y[k + 1] - y[k])
                k += 1
            dx, dy = x[k + 1] - x[k], y[k + 1] - y[k]
            sl = math.hypot(dx, dy)
            if sl == 0:
                ux, uy, f = 0.0, 0.0, 0.0
            else:
                ux, uy, f = dx / sl, dy / sl, min(1.0, max(0.0, (target - acc) / sl))
            bx, by = x[k] + dx * f, y[k] + dy * f
            for o in offsets:
                px[j] = bx - uy * o
                py[j] = by + ux * o
                pe[j] = e
                j += 1
    return px, py, pe


def step_prepare(args):
    import shapely
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components

    with open(work("osm.pkl"), "rb") as f:
        osm = pickle.load(f)
    fwd, _ = projector()
    (cx, cy), circ, _ = region_circle(args.center, args.radius)
    tile_dir = work("tiles", "x")[:-1]
    remember_canopy_bounds(tile_dir)
    for d in (tile_dir, os.path.join(C.WORK_DIR, "shade")):       # old tiles and results no longer match
        for f in os.listdir(d) if os.path.isdir(d) else []:
            os.remove(os.path.join(d, f))

    # --- streets: split ways at intersections, like osmnx's simplified graph ---
    ways = [w for w in osm["ways"].values() if road_ok(w[4])]
    refs = np.concatenate([w[0] for w in ways])
    lon = np.concatenate([w[1] for w in ways])
    lat = np.concatenate([w[2] for w in ways])
    offs = np.r_[0, np.cumsum([len(w[0]) for w in ways])]
    x, y = fwd.transform(lon, lat)
    uniq, cnt = np.unique(refs, return_counts=True)
    is_node = np.isin(refs, uniq[cnt > 1])
    is_node[offs[:-1]] = True
    is_node[offs[1:] - 1] = True
    ep, eq, ew = [], [], []
    for k in range(len(ways)):
        pos = np.flatnonzero(is_node[offs[k]:offs[k + 1]]) + offs[k]
        for p, q in zip(pos[:-1], pos[1:]):
            if refs[p] != refs[q]:                      # drop loops, as street_segments() does
                ep.append(p); eq.append(q); ew.append(k)
    ep, eq, ew = np.array(ep), np.array(eq), np.array(ew)
    seglen = np.hypot(np.diff(x), np.diff(y))
    csum = np.r_[0, np.cumsum(seglen)]
    elen = csum[eq] - csum[ep]
    mx, my = (x[ep] + x[eq]) / 2, (y[ep] + y[eq]) / 2
    keep = np.hypot(mx - cx, my - cy) <= args.radius
    # routes use only public walkable streets in the biggest connected network, as osmnx does;
    # private and cut-off roads still get shade colours
    public = np.array([ways[k][5] if len(ways[k]) > 5 else True for k in ew], bool)
    u, v = refs[ep], refs[eq]
    ids, inv = np.unique(np.r_[u, v], return_inverse=True)
    ui, vi = inv[:len(u)], inv[len(u):]
    pk = keep & public
    g = coo_matrix((np.ones(pk.sum()), (ui[pk], vi[pk])), shape=(len(ids), len(ids)))
    _, comp = connected_components(g, directed=False)
    big = np.bincount(comp[ui[pk]]).argmax()
    walk = pk & (comp[ui] == big)
    ep, eq, ew, elen, walk = ep[keep], eq[keep], ew[keep], elen[keep], walk[keep]
    log(f"  {len(ep):,} street segments, {elen.sum() / 1000:,.0f} km of roads and paths "
        f"({elen[walk].sum() / 1000:,.0f} km used for walking routes)")

    # --- sample points along every segment (three lines across the 8 m corridor) ---
    counts = _count_samples(x, y, ep, eq, C.SAMPLE_SPACING_M)
    px, py, pe = _sample(x, y, ep, eq, counts, np.array(C.LATERAL_OFFSETS_M))
    log(f"  {len(px):,} sample points")

    # --- buildings and trees in metres ---
    def proj(geoms):
        return shapely.transform(geoms, lambda c: np.column_stack(fwd.transform(c[:, 0], c[:, 1])))
    b = osm["buildings"]
    bgeom = proj(shapely.from_wkb([g for g, _ in b])) if b else np.array([], object)
    bh = np.array([h for _, h in b], np.float32)
    tg = []
    for kind, g in osm["trees"]:
        tg.append(shapely.Point(g) if kind == "pt" else shapely.from_wkb(g))
    tgeom = proj(np.array(tg, object)) if tg else np.array([], object)
    if len(tgeom):
        small = shapely.get_type_id(tgeom) < 3            # points and lines become 4 m circles
        tgeom[small] = shapely.buffer(tgeom[small], C.OSM_TREE_RADIUS, quad_segs=4)
    btree, ttree = shapely.STRtree(bgeom), shapely.STRtree(tgeom)

    # --- tiles ---
    x0, y0 = cx - args.radius, cy - args.radius
    tx = ((mx[keep] - x0) // C.TILE_M).astype(int)
    ty = ((my[keep] - y0) // C.TILE_M).astype(int)
    tile_of = tx * 10000 + ty
    order = np.argsort(tile_of[pe], kind="stable")
    pts_split = np.split(order, np.flatnonzero(np.diff(tile_of[pe][order])) + 1)
    n_tiles = 0
    for idx in pts_split:
        tid = int(tile_of[pe[idx[0]]])
        segs = np.unique(pe[idx])
        a, bnd = int(tid // 10000), int(tid % 10000)
        bx0, by0 = x0 + a * C.TILE_M, y0 + bnd * C.TILE_M
        vx = np.concatenate([x[ep[s]:eq[s] + 1] for s in segs])
        vy = np.concatenate([y[ep[s]:eq[s] + 1] for s in segs])
        m = C.SHADOW_MAX_M + 5
        bounds = (math.floor(min(bx0, vx.min()) - m), math.floor(min(by0, vy.min()) - m),
                  math.ceil(max(bx0 + C.TILE_M, vx.max()) + m), math.ceil(max(by0 + C.TILE_M, vy.max()) + m))
        area = shapely.box(*bounds)
        bi, ti = btree.query(area), ttree.query(area)
        local = np.searchsorted(segs, pe[idx])
        with open(os.path.join(tile_dir, f"{a}_{bnd}.pkl"), "wb") as f:
            pickle.dump({"tile": f"{a}_{bnd}", "bounds": bounds,
                         "bwkb": shapely.to_wkb(bgeom[bi]), "bh": bh[bi], "twkb": shapely.to_wkb(tgeom[ti]),
                         "px": px[idx], "py": py[idx], "pseg": local, "seg": segs}, f, protocol=5)
        n_tiles += 1

    places = [p for p in osm["places"] if circ.contains(shapely.Point(*fwd.transform(p[3], p[4])))]
    with open(work("network.pkl"), "wb") as f:
        pickle.dump({"refs": refs, "lon": lon, "lat": lat, "ep": ep, "eq": eq, "len": elen, "walk": walk,
                     "name": [ways[k][3] for k in ew], "highway": [ways[k][4] for k in ew],
                     "places": places, "buildings": len(b), "trees": len(tg)}, f, protocol=5)
    log(f"  {n_tiles} tiles of {C.TILE_M / 1000:g} km ready")


# =====================================================================
# 4. Shade: every street, every week, every hour
# =====================================================================
def sun_slots(center):
    """Sun elevation and azimuth for every (week, hour) slot, with pvlib as in shade_lib.sun_position."""
    from shade_lib import sun_position
    el, az = [], []
    for d in C.slot_dates():
        for h in C.HOURS:
            e, a = sun_position(f"{d.isoformat()} {h:02d}:00", center[0], center[1])
            el.append(e); az.append(a)
    return np.array(el), np.array(az)


BLOCK = 8                                                 # coarse blocks used to skip empty ground fast


@njit(cache=True)
def shade_points(hgt, hblock, canopy, x0, ytop, res, px, py, pseg, nseg, tan_el, sx, sy, dmax, hmax):
    """For every street piece and sun position: how many of its points are in shade (under a
    building or a tree, or a building between the point and the sun reaches above the sun line)."""
    n, S = px.shape[0], tan_el.shape[0]
    R, W = hgt.shape
    out = np.zeros((nseg, S), np.uint16)
    for i in range(n):
        g = pseg[i]
        c, r = (px[i] - x0) / res, (ytop - py[i]) / res
        ci, ri = int(math.floor(c)), int(math.floor(r))
        if 0 <= ri < R and 0 <= ci < W and (hgt[ri, ci] > 0 or canopy[ri, ci]):
            for s in range(S):
                out[g, s] += 1
            continue
        for s in range(S):
            t = tan_el[s]
            if t <= 0:
                out[g, s] += 1                            # sun is down: all shade, as in shade_lib
                continue
            lim = min(dmax, hmax / t)
            vx, vy = sx[s], -sy[s]                        # ray direction in raster columns / rows
            d = res
            while d <= lim:
                fc, fr = c + vx * d / res, r + vy * d / res
                cc, rr = int(math.floor(fc)), int(math.floor(fr))
                if rr < 0 or rr >= R or cc < 0 or cc >= W:
                    break
                bc, br = cc // BLOCK, rr // BLOCK
                if hblock[br, bc] <= d * t:
                    # nothing in this block can reach the sun line any more: jump to where the ray leaves it
                    ex = ((bc + 1) * BLOCK - fc) / vx if vx > 1e-9 else ((fc - bc * BLOCK) / -vx if vx < -1e-9 else 1e9)
                    ey = ((br + 1) * BLOCK - fr) / vy if vy > 1e-9 else ((fr - br * BLOCK) / -vy if vy < -1e-9 else 1e9)
                    d += max(min(ex, ey) * res + 1e-3, 1e-3)
                    continue
                if hgt[rr, cc] > d * t:
                    out[g, s] += 1
                    break
                d += res
    return out


def remember_canopy_bounds(tile_dir):
    """Older canopy files didn't store the area they cover. Add it from the old tiles before they're replaced."""
    if not os.path.isdir(tile_dir):
        return
    for f in os.listdir(tile_dir):
        cache = os.path.join(C.WORK_DIR, "canopy", f[:-4] + ".npz")
        if not f.endswith(".pkl") or not os.path.exists(cache):
            continue
        try:
            with np.load(cache) as z:
                if "bounds" in z:
                    continue
                bits = z["bits"]
            with open(os.path.join(tile_dir, f), "rb") as fh:
                bounds = pickle.load(fh)["bounds"]
            np.savez_compressed(cache[:-4] + ".tmp.npz", bits=bits, bounds=np.array(bounds))
            os.replace(cache[:-4] + ".tmp.npz", cache)
        except Exception:
            pass


def _cached_canopy(cache, bounds, shape):
    """The saved canopy cut to these bounds, and how much of them it covers (0..1)."""
    with np.load(cache) as z:
        if "bounds" not in z:
            return None, 0.0
        cb = [int(v) for v in z["bounds"]]
        ch, cw = int(round((cb[3] - cb[1]) / C.GRID_M)), int(round((cb[2] - cb[0]) / C.GRID_M))
        old = np.unpackbits(z["bits"])[:ch * cw].reshape(ch, cw).astype(bool)
    x0, y0, x1, y1 = bounds
    ox0, oy0, ox1, oy1 = max(x0, cb[0]), max(y0, cb[1]), min(x1, cb[2]), min(y1, cb[3])
    out = np.zeros(shape, bool)
    if ox1 <= ox0 or oy1 <= oy0:
        return out, 0.0
    out[y1 - oy1:y1 - oy0, ox0 - x0:ox1 - x0] = old[cb[3] - oy1:cb[3] - oy0, ox0 - cb[0]:ox1 - cb[0]]
    return out, (ox1 - ox0) * (oy1 - oy0) / ((x1 - x0) * (y1 - y0))


def canopy_mask(tile, bounds, shape, transform):
    """Satellite canopy (Meta/WRI, the same source as shade_lib.load_canopy) for one tile, cached.
    Returns None if it could not be downloaded (offline); the tile is then retried next run."""
    cache = work("canopy", tile + ".npz")
    saved = None
    if os.path.exists(cache):
        try:
            saved, cover = _cached_canopy(cache, bounds, shape)
            if saved is not None and cover >= 0.9:
                return saved                                  # the edges only matter for shadows, not canopy
        except Exception:
            os.remove(cache)                                  # a broken file from an earlier stop: fetch again
            saved = None
    import mercantile
    import rasterio
    from affine import Affine
    from rasterio.errors import RasterioIOError, WindowError
    from rasterio.warp import Resampling, reproject, transform_bounds
    from rasterio.windows import Window, from_bounds
    from shade_lib import CANOPY_URL, CANOPY_ZOOM

    best = np.zeros(shape, np.float32)
    west, south, east, north = transform_bounds(C.PROJ_CRS, "EPSG:4326", *bounds)
    ok = failed = 0
    with rasterio.Env(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR", GDAL_HTTP_MAX_RETRY="4", GDAL_HTTP_RETRY_DELAY="2"):
        for t in mercantile.tiles(west, south, east, north, CANOPY_ZOOM):
            try:
                with rasterio.open(CANOPY_URL.format(qk=mercantile.quadkey(t))) as src:
                    win = from_bounds(*transform_bounds(C.PROJ_CRS, src.crs, *bounds), transform=src.transform)
                    try:
                        win = win.round_offsets().round_lengths().intersection(Window(0, 0, src.width, src.height))
                    except WindowError:
                        ok += 1
                        continue
                    k = C.CANOPY_DOWNSAMPLE                 # read a lighter overview: about k*k times less to download
                    oh, ow = max(1, int(win.height // k)), max(1, int(win.width // k))
                    arr = np.nan_to_num(src.read(1, window=win, out_shape=(oh, ow)).astype(np.float32))
                    src_tr = src.window_transform(win) * Affine.scale(win.width / ow, win.height / oh)
                    part = np.zeros(shape, np.float32)
                    reproject(arr, part, src_transform=src_tr, src_crs=src.crs,
                              dst_transform=transform, dst_crs=C.PROJ_CRS, resampling=Resampling.nearest)
                    np.maximum(best, part, out=best)
                    ok += 1
            except RasterioIOError:
                failed += 1
    if ok == 0:
        return saved                                          # offline: use what was saved, if anything
    mask = best >= C.CANOPY_MIN_HEIGHT
    np.savez_compressed(cache[:-4] + ".tmp.npz", bits=np.packbits(mask.ravel()), bounds=np.array(bounds))
    os.replace(cache[:-4] + ".tmp.npz", cache)
    return mask


def shade_tile(path, tan_el, sx, sy, use_canopy):
    import shapely
    from rasterio.features import rasterize
    from rasterio.transform import from_origin

    with open(path, "rb") as f:
        d = pickle.load(f)
    x0, y0, x1, y1 = d["bounds"]
    shape = (int(round((y1 - y0) / C.GRID_M)), int(round((x1 - x0) / C.GRID_M)))
    transform = from_origin(x0, y1, C.GRID_M, C.GRID_M)

    hgt = np.zeros(shape, np.float32)
    if len(d["bh"]):
        geoms = shapely.from_wkb(d["bwkb"])
        order = np.argsort(d["bh"])                       # taller buildings win where outlines overlap
        rasterize(zip(geoms[order], d["bh"][order].astype(float)), out=hgt, transform=transform)
    trees = np.zeros(shape, bool)
    if len(d["twkb"]):
        trees = rasterize(((g, 1) for g in shapely.from_wkb(d["twkb"])), out_shape=shape,
                          transform=transform, dtype="uint8").astype(bool)
    sat = canopy_mask(d["tile"], d["bounds"], shape, transform) if use_canopy else None
    got_canopy = sat is not None or not use_canopy
    canopy = trees | sat if sat is not None else trees
    canopy &= hgt == 0                                    # canopy on rooftops is removed, as in clean_canopy

    H, W = shape
    pad = np.zeros((-(-H // BLOCK) * BLOCK, -(-W // BLOCK) * BLOCK), np.float32)
    pad[:H, :W] = hgt
    hblock = pad.reshape(pad.shape[0] // BLOCK, BLOCK, pad.shape[1] // BLOCK, BLOCK).max(axis=(1, 3))
    n = len(d["seg"])
    del trees, sat, pad
    sums = shade_points(hgt, hblock, canopy.view(np.uint8), float(x0), float(y1), C.GRID_M, d["px"], d["py"],
                        d["pseg"].astype(np.int64), n, tan_el, sx, sy, float(C.SHADOW_MAX_M), float(max(hgt.max(), 0.1)))
    frac = sums / np.bincount(d["pseg"], minlength=n)[:, None]
    out_path = work("shade", d["tile"] + ".npz")
    np.savez_compressed(out_path[:-4] + ".tmp.npz", seg=d["seg"],      # write, then rename: a stop can't leave half a file
                        shade=np.round(frac * C.SHADE_SCALE).astype(np.uint8), canopy=np.array(got_canopy))
    os.replace(out_path[:-4] + ".tmp.npz", out_path)
    return d["tile"], len(d["px"]), got_canopy


def free_memory_gb():
    try:
        if os.name == "nt":
            import ctypes

            class MS(ctypes.Structure):
                _fields_ = [("len", ctypes.c_ulong), ("load", ctypes.c_ulong), ("total", ctypes.c_ulonglong),
                            ("avail", ctypes.c_ulonglong), ("tp", ctypes.c_ulonglong), ("ap", ctypes.c_ulonglong),
                            ("tv", ctypes.c_ulonglong), ("av", ctypes.c_ulonglong), ("ext", ctypes.c_ulonglong)]
            m = MS()
            m.len = ctypes.sizeof(MS)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
            return m.avail / 1e9
        with open("/proc/meminfo") as f:
            return int(next(l for l in f if l.startswith("MemAvailable")).split()[1]) / 1e6
    except Exception:
        return None


def auto_workers():
    """About 0.7 GB per worker, keeping 2 GB free, and leaving two CPU threads for you."""
    cpus = max(1, (os.cpu_count() or 2) - 2)
    free = free_memory_gb()
    by_mem = max(1, int((free - 2.0) / 0.7)) if free else 4
    return max(1, min(cpus, by_mem, 10))


def low_priority():
    try:
        if os.name == "nt":
            import ctypes
            k = ctypes.windll.kernel32
            k.SetPriorityClass(k.GetCurrentProcess(), 0x00004000)       # below normal
        else:
            os.nice(10)
    except Exception:
        pass


def step_shade(args):
    tiles_dir = os.path.join(C.WORK_DIR, "tiles")
    tiles = sorted(f for f in os.listdir(tiles_dir) if f.endswith(".pkl"))
    todo = []
    for f in tiles:
        out = os.path.join(C.WORK_DIR, "shade", f[:-4] + ".npz")
        if os.path.exists(out):
            try:
                with np.load(out) as z:
                    ok = z["shade"].shape[0] == len(z["seg"]) and (not args.redo_no_canopy or bool(z["canopy"]))
            except Exception:
                ok = False                                    # half-written when the run was stopped
            if ok:
                continue
            os.remove(out)
        todo.append(os.path.join(tiles_dir, f))
    log(f"  {len(tiles) - len(todo)} of {len(tiles)} tiles already done")
    if not todo:
        return
    el, az = sun_slots(args.center)
    tan_el = np.where(el > 0, np.tan(np.radians(np.maximum(el, 0.01))), -1.0)
    sx, sy = np.sin(np.radians(az)), np.cos(np.radians(az))   # towards the sun (x east, y north)
    use_canopy = not args.no_canopy
    t0, done, no_can = time.time(), 0, 0
    workers = args.workers or auto_workers()
    log(f"  Using {workers} workers (change with --workers N). They run at low priority, so the laptop stays usable.")
    pool = ProcessPoolExecutor(max_workers=workers, initializer=low_priority)
    pending = {pool.submit(shade_tile, p, tan_el, sx, sy, use_canopy) for p in todo}
    try:
        while pending:
            # wake up every second, so Ctrl+C works on Windows too
            finished, pending = wait(pending, timeout=1.0, return_when=FIRST_COMPLETED)
            for fut in finished:
                tile, npts, can = fut.result()
                done += 1
                no_can += use_canopy and not can
                rate = (time.time() - t0) / done
                log(f"  tile {tile}: {npts:,} points  ({done}/{len(todo)}, about "
                    f"{rate * (len(todo) - done) / 60:.0f} min left)")
    except KeyboardInterrupt:
        log("\n  Stopping... finished tiles are saved. Run the same command again to continue.")
        for f in pending:
            f.cancel()
        for proc in list(getattr(pool, "_processes", {}).values()):
            proc.terminate()
        pool.shutdown(wait=False, cancel_futures=True)
        sys.exit(1)
    pool.shutdown()
    if no_can:
        log(f"  {no_can} tiles have no satellite canopy (offline?). Run again later with --redo-no-canopy.")


# =====================================================================
# 5. Pack everything into one SQLite file for the backend
# =====================================================================
def step_pack(args):
    with open(work("network.pkl"), "rb") as f:
        net = pickle.load(f)
    refs, lon, lat, ep, eq = net["refs"], net["lon"], net["lat"], net["ep"], net["eq"]
    walk = net.get("walk", np.ones(len(ep), bool))
    S = C.WEEKS * len(C.HOURS)
    shade = np.zeros((len(ep), S), np.uint8)
    have = np.zeros(len(ep), bool)
    no_canopy = 0
    shade_dir = os.path.join(C.WORK_DIR, "shade")
    for f in os.listdir(shade_dir):
        if f.endswith(".tmp.npz"):
            continue
        z = np.load(os.path.join(shade_dir, f))
        shade[z["seg"]] = z["shade"]
        have[z["seg"]] = True
        no_canopy += not bool(z["canopy"])
    if not have.all():
        sys.exit(f"{(~have).sum():,} segments have no shade yet. Run: python build_region.py --step shade")

    tmp = C.DB_PATH + ".tmp"
    if os.path.exists(tmp):
        os.remove(tmp)
    db = sqlite3.connect(tmp)
    db.executescript("""
        CREATE TABLE meta (k TEXT PRIMARY KEY, v TEXT);
        CREATE TABLE nodes (id INTEGER PRIMARY KEY, lon REAL, lat REAL, gx INTEGER, gy INTEGER);
        CREATE TABLE segs (id INTEGER PRIMARY KEY, u INTEGER, v INTEGER, len REAL, name TEXT, highway TEXT,
                           gx INTEGER, gy INTEGER, coords BLOB, shade BLOB, walk INTEGER);
        CREATE TABLE places (id INTEGER PRIMARY KEY, name TEXT, name_en TEXT, cls TEXT, lon REAL, lat REAL,
                             gx INTEGER, gy INTEGER);
    """)
    G = C.LOOKUP_GRID_DEG
    rows = []
    for i in range(len(ep)):
        a, b = ep[i], eq[i] + 1
        lo, la = lon[a:b], lat[a:b]
        coords = np.empty(2 * (b - a), np.int32)
        coords[0::2], coords[1::2] = np.round(lo * 1e6), np.round(la * 1e6)
        rows.append((i, int(refs[ep[i]]), int(refs[eq[i]]), round(float(net["len"][i]), 1), net["name"][i],
                     net["highway"][i], int(math.floor(lo.mean() / G)), int(math.floor(la.mean() / G)),
                     coords.tobytes(), zlib.compress(shade[i].tobytes(), 6), int(walk[i])))
        if len(rows) == 50_000:
            db.executemany("INSERT INTO segs VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows)
            rows = []
    db.executemany("INSERT INTO segs VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows)
    node_idx = np.unique(np.r_[ep[walk], eq[walk]])
    _, first = np.unique(refs[node_idx], return_index=True)
    node_idx = node_idx[first]
    db.executemany("INSERT INTO nodes VALUES (?,?,?,?,?)",
                   ((int(refs[k]), float(lon[k]), float(lat[k]), int(math.floor(lon[k] / G)),
                     int(math.floor(lat[k] / G))) for k in node_idx))
    db.executemany("INSERT INTO places (name, name_en, cls, lon, lat, gx, gy) VALUES (?,?,?,?,?,?,?)",
                   ((n, ne, c, x, y, math.floor(x / G), math.floor(y / G)) for n, ne, c, x, y in net["places"]))
    db.executescript("""
        CREATE INDEX segs_grid ON segs (gx, gy);
        CREATE INDEX nodes_grid ON nodes (gx, gy);
        CREATE INDEX places_grid ON places (gx, gy);
    """)
    w = net["len"] / net["len"].sum()
    avg = np.zeros(S)
    for i in range(0, len(ep), 100_000):                     # in chunks, to keep memory low
        avg += w[i:i + 100_000] @ shade[i:i + 100_000].astype(np.float32)
    avg = (avg / C.SHADE_SCALE).reshape(C.WEEKS, len(C.HOURS))
    meta = {"key": C.KEY, "name": C.NAME, "center": list(args.center), "radius": args.radius,
            "weeks": C.WEEKS, "hours": C.HOURS, "ref_year": C.REF_YEAR, "scale": C.SHADE_SCALE,
            "week_days": [d.isoformat() for d in C.slot_dates()], "grid_deg": G,
            "segments": int(len(ep)), "street_km": round(float(net["len"].sum()) / 1000, 1),
            "walk_km": round(float(net["len"][walk].sum()) / 1000, 1),
            "buildings": net["buildings"], "mapped_trees": net["trees"], "places": len(net["places"]),
            "tiles_without_canopy": no_canopy, "built": time.strftime("%Y-%m-%d %H:%M"),
            "average_shade": np.round(avg, 3).tolist()}
    db.executemany("INSERT INTO meta VALUES (?,?)", [(k, json.dumps(v)) for k, v in meta.items()])
    db.commit()
    db.close()
    os.replace(tmp, C.DB_PATH)
    log(f"  Saved {C.DB_PATH} ({os.path.getsize(C.DB_PATH) / 1e6:,.0f} MB): {len(ep):,} segments, "
        f"{meta['street_km']:,} km of streets, {len(net['places']):,} places")
    may = C.slot_dates().index(min(C.slot_dates(), key=lambda d: abs(d.month * 31 + d.day - (5 * 31 + 15))))
    log("  Average street shade in mid-May: " +
        ", ".join(f"{h % 12 or 12}{'am' if h < 12 else 'pm'} {v:.0%}" for h, v in zip(C.HOURS, avg[may])))


def main():
    p = argparse.ArgumentParser(description="Build offline shade data for a big circle.")
    p.add_argument("--step", choices=STEPS, help="run only this step")
    p.add_argument("--from-step", choices=STEPS, help="start from this step")
    p.add_argument("--pbf", nargs="+", help="use these .osm.pbf files instead of downloading")
    p.add_argument("--center", help="lat,lon (default from region_config.py)")
    p.add_argument("--radius", type=float, help="metres (default from region_config.py)")
    p.add_argument("--workers", type=int, default=0, help="CPU workers for the shade step (default: picked from free memory)")
    p.add_argument("--no-canopy", action="store_true", help="skip satellite canopy (fully offline build)")
    p.add_argument("--redo-no-canopy", action="store_true", help="redo tiles that missed the satellite canopy")
    args = p.parse_args()
    args.center = tuple(map(float, args.center.split(","))) if args.center else C.CENTER
    args.radius = args.radius or C.RADIUS_M
    os.makedirs(C.WORK_DIR, exist_ok=True)

    steps = [args.step] if args.step else STEPS[STEPS.index(args.from_step or "download"):]
    for s in steps:
        marker = {"download": "pbfs.json", "osm": "osm.pkl", "prepare": "network.pkl"}.get(s)
        if not args.step and not args.from_step and not args.pbf and marker and os.path.exists(work(marker)):
            log(f"\n=== {s} === already done (run with --step {s} to redo it)")
            continue
        log(f"\n=== {s} ===")
        t0 = time.time()
        globals()["step_" + s](args)
        log(f"  done in {(time.time() - t0) / 60:.1f} min")


if __name__ == "__main__":
    main()
