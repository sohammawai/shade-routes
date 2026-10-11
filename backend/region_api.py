"""Offline endpoints for the big region. Add to main.py with two lines:

    from region_api import router as region_router
    app.include_router(region_router)

/api/region/info | shade | route | places | street/{id}   shade data and routing, no internet needed
/offline/tiles/{z}/{x}/{y}.pbf, /offline/fonts/...         the base map, from data/region/basemap.mbtiles
/offline/tiles.json                                          the TileJSON the app's map style points at
/app                                                         the app itself (backend/static/index.html)

Map tiles or fonts that were not downloaded yet are fetched from OpenFreeMap when the
laptop is online, and saved, so the offline map fills in as you use it.
"""
import gzip
import json
import os
import sqlite3
import threading
import urllib.request
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from region_engine import HERE, get_region

router = APIRouter()
OFM = "https://tiles.openfreemap.org"
STATIC = os.path.join(HERE, "static", "index.html")


def _region():
    r = get_region()
    if r is None:
        raise HTTPException(404, "No offline region yet. Run: python build_region.py")
    return r


@router.get("/api/region/info")
def region_info():
    return _region().info()


@router.get("/api/region/shade")
def region_shade(west: float, south: float, east: float, north: float, when: Optional[str] = None):
    if (east - west) * (north - south) > 0.0012:            # about 3.5 km x 3.5 km
        raise HTTPException(400, "Zoom in to see street shade.")
    return _region().shade_geojson(west, south, east, north, when)


@router.get("/api/region/route")
def region_route(from_lat: float, from_lon: float, to_lat: float, to_lon: float, when: Optional[str] = None):
    try:
        return _region().route(from_lat, from_lon, to_lat, to_lon, when)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/api/region/places")
def region_places(q: str = Query(..., min_length=1), lat: Optional[float] = None, lon: Optional[float] = None,
                  limit: int = 10):
    return _region().places(q, lat, lon, min(limit, 30))


@router.get("/api/region/nearby")
def region_nearby(lat: float, lon: float, cats: str = "park,cafe,food,transit", limit: int = 50, offset: int = 0):
    """Places of each kind across the whole saved area, nearest first, with how many there are in total."""
    return _region().nearby(lat, lon, [c.strip() for c in cats.split(",") if c.strip()],
                            max(1, min(limit, 500)), max(0, offset))


@router.get("/api/region/planner")
def region_planner(west: float, south: float, east: float, north: float, when: Optional[str] = None,
                   limit: int = 15):
    if (east - west) * (north - south) > 0.0025:
        raise HTTPException(400, "Zoom in to plan streets.")
    return _region().planner(west, south, east, north, when, max(1, min(limit, 30)))


class RegionChat(BaseModel):
    message: str = Field(..., min_length=1, max_length=1500)
    history: list = []
    lat: Optional[float] = None
    lon: Optional[float] = None
    when: Optional[str] = None


@router.post("/api/region/chat")
def region_chat(req: RegionChat):
    import region_chat as rc
    return rc.answer(req.message, req.history, req.lat, req.lon, req.when)


@router.get("/api/region/street/{seg_id}")
def region_street(seg_id: int, date: Optional[str] = None):
    try:
        return _region().street_day(seg_id, date)
    except KeyError:
        raise HTTPException(404, "No such street piece")


# ---------------- offline base map ----------------
class Basemap:
    def __init__(self):
        r = get_region()
        folder = os.path.dirname(r.path) if r else os.path.join(HERE, "..", "data", "region")
        os.makedirs(folder, exist_ok=True)
        self.path = os.path.join(folder, "basemap.mbtiles")
        self.db = sqlite3.connect(self.path, check_same_thread=False)
        self.lock = threading.Lock()
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS metadata (name TEXT PRIMARY KEY, value TEXT);
            CREATE TABLE IF NOT EXISTS tiles (zoom_level INTEGER, tile_column INTEGER, tile_row INTEGER,
                                              tile_data BLOB, PRIMARY KEY (zoom_level, tile_column, tile_row));
            CREATE TABLE IF NOT EXISTS fonts (stack TEXT, range TEXT, data BLOB, PRIMARY KEY (stack, range));
        """)
        self.template = None

    def get(self, sql, args):
        with self.lock:
            row = self.db.execute(sql, args).fetchone()
        return row[0] if row else None

    def put(self, sql, args):
        with self.lock:
            self.db.execute(sql, args)
            self.db.commit()

    def tile_url(self):
        if self.template is None:
            self.template = self.get("SELECT value FROM metadata WHERE name = 'tiles_url'", ())
            if not self.template:
                tj = json.loads(_fetch(OFM + "/planet"))
                self.template = tj["tiles"][0]
                self.put("INSERT OR REPLACE INTO metadata VALUES ('tiles_url', ?)", (self.template,))
        return self.template


def _fetch(url, timeout=8):
    req = urllib.request.Request(url, headers={"User-Agent": "ShadeRoutes-offline/1.0", "Accept-Encoding": "gzip"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = r.read()
        return gzip.decompress(data) if r.headers.get("Content-Encoding") == "gzip" else data


_basemap = None


def basemap():
    global _basemap
    if _basemap is None:
        _basemap = Basemap()
    return _basemap


@router.get("/offline/tiles.json")
def offline_tilejson(request: Request):
    base = str(request.base_url).rstrip("/")
    return {"tilejson": "3.0.0", "tiles": [base + "/offline/tiles/{z}/{x}/{y}.pbf"], "minzoom": 0, "maxzoom": 14,
            "attribution": "© OpenMapTiles © OpenStreetMap contributors (offline copy)"}


@router.get("/offline/tiles/{z}/{x}/{y}.pbf")
def offline_tile(z: int, x: int, y: int):
    bm = basemap()
    row = (1 << z) - 1 - y                                  # MBTiles counts rows from the bottom
    data = bm.get("SELECT tile_data FROM tiles WHERE zoom_level=? AND tile_column=? AND tile_row=?", (z, x, row))
    if data is None:
        try:
            data = _fetch(bm.tile_url().format(z=z, x=x, y=y))
            bm.put("INSERT OR REPLACE INTO tiles VALUES (?,?,?,?)", (z, x, row, data))
        except Exception:
            return Response(status_code=204)                # offline and not saved: an empty tile
    if data[:2] == b"\x1f\x8b":
        data = gzip.decompress(data)
    return Response(data, media_type="application/x-protobuf", headers={"Cache-Control": "max-age=86400"})


@router.get("/offline/fonts/{stack}/{rng}.pbf")
def offline_font(stack: str, rng: str):
    bm = basemap()
    data = bm.get("SELECT data FROM fonts WHERE stack=? AND range=?", (stack, rng))
    if data is None:
        try:
            data = _fetch(f"{OFM}/fonts/{urllib.request.quote(stack)}/{rng}.pbf")
            bm.put("INSERT OR REPLACE INTO fonts VALUES (?,?,?)", (stack, rng, data))
        except Exception:
            data = b""                                      # no letters for this range while offline
    return Response(data, media_type="application/x-protobuf", headers={"Cache-Control": "max-age=86400"})


@router.get("/app")
def offline_app():
    if not os.path.exists(STATIC):
        raise HTTPException(404, "Put the app page at backend/static/index.html")
    return FileResponse(STATIC, media_type="text/html")
