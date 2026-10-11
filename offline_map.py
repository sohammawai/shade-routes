"""Save the base map (streets, parks, metro, labels) for the region so the app works offline.

Usage (from the shade-routes folder, venv active, while online):
    python offline_map.py                 zoom 0-14 for the whole circle (about 2,000 tiles, 100-200 MB)
    python offline_map.py --max-zoom 13   smaller

Tiles come from OpenFreeMap (free, no key). It downloads slowly on purpose to be polite,
and skips tiles it already has, so you can stop and re-run it.
Saves to data/region/basemap.mbtiles, which backend/region_api.py serves.
"""
import argparse
import math
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "backend"))
os.environ.setdefault("REGION_DB", os.path.join("data", "region", "region.db"))

import region_config as C                      # noqa: E402
import region_api as A                         # noqa: E402

FONTS = ["Noto Sans Regular", "Noto Sans Bold", "Noto Sans Italic"]
# Latin, Latin extended, Greek/Cyrillic, Devanagari (Hindi), punctuation, symbols
RANGES = [0, 256, 512, 768, 1024, 2304, 8192, 8448, 8704, 9472, 65024, 65280]


def tiles_for(center, radius, zmax):
    lat, lon = center
    dlat = radius / 111_320
    dlon = radius / (111_320 * math.cos(math.radians(lat)))
    west, east, south, north = lon - dlon, lon + dlon, lat - dlat, lat + dlat

    def xy(lo, la, z):
        n = 1 << z
        x = int((lo + 180) / 360 * n)
        y = int((1 - math.asinh(math.tan(math.radians(la))) / math.pi) / 2 * n)
        return min(max(x, 0), n - 1), min(max(y, 0), n - 1)
    for z in range(zmax + 1):
        x0, y0 = xy(west, north, z)
        x1, y1 = xy(east, south, z)
        for x in range(x0, x1 + 1):
            for y in range(y0, y1 + 1):
                yield z, x, y


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--max-zoom", type=int, default=14)
    p.add_argument("--threads", type=int, default=4)
    args = p.parse_args()
    bm = A.basemap()
    tiles = list(tiles_for(C.CENTER, C.RADIUS_M, args.max_zoom))
    have = {(z, x, (1 << z) - 1 - r) for z, x, r in
            bm.db.execute("SELECT zoom_level, tile_column, tile_row FROM tiles")}
    todo = [t for t in tiles if t not in have]
    print(f"{len(tiles):,} map tiles for the circle, {len(todo):,} still to download")
    t0, done, failed = time.time(), 0, 0

    def one(t):
        r = A.offline_tile(*t)
        time.sleep(0.05)
        return r.status_code == 200
    with ThreadPoolExecutor(args.threads) as pool:
        for ok in pool.map(one, todo):
            done += 1
            failed += not ok
            if done % 100 == 0 or done == len(todo):
                print(f"  {done:,}/{len(todo):,}  ({time.time() - t0:.0f} s)")
    for f in FONTS:
        for r in RANGES:
            A.offline_font(f, f"{r}-{r + 255}")
    size = os.path.getsize(bm.path) / 1e6
    print(f"Saved {bm.path} ({size:,.0f} MB). {failed} tiles failed; run again to retry them.")


if __name__ == "__main__":
    main()
