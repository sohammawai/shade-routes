"""Settings for the big offline region (built by build_region.py, read by backend/region_engine.py).

Change CENTER or RADIUS_M here to build a different circle. Everything else can stay as it is.
"""
import os

KEY = "delhi_40km"
NAME = "Delhi NCR (40 km around Chhatarpur)"
CENTER = (28.4977, 77.1840)          # (lat, lon): Chhatarpur, not an exact address
RADIUS_M = 40_000

# Shade model: the same rules as shade_lib.py / build_area.py
HALF_WIDTH = 4.0                     # 8 m walking corridor, as in build_area.py
SHADOW_MAX_M = 300                   # building shadows are capped at 300 m, as in shade_lib.building_shadows
CANOPY_MIN_HEIGHT = 3.0              # satellite canopy counts as a tree from 3 m, as in shade_lib.load_canopy
CANOPY_DOWNSAMPLE = 2                # read the satellite canopy at about 2 m (1 = full detail, 4x more download)
OSM_TREE_RADIUS = 4.0                # a mapped single tree is a 4 m circle, as in shade_lib.load_trees

# Time slots: every week of the year x every hour from 6 AM to 7 PM
WEEKS = 52
HOURS = list(range(6, 20))
REF_YEAR = 2026                      # sun positions repeat almost exactly every year
SHADE_SCALE = 200                    # shade is stored as 0..200 (0.5 % steps) in one byte

# How the work is cut up
TILE_M = 2000                        # 2 km x 2 km tiles
GRID_M = 1.0                         # 1 m raster for buildings and canopy
SAMPLE_SPACING_M = 5.0               # points every 5 m along a street...
LATERAL_OFFSETS_M = (-HALF_WIDTH * 2 / 3, 0.0, HALF_WIDTH * 2 / 3)   # ...on three lines across the corridor
LOOKUP_GRID_DEG = 0.02               # the backend finds streets by 0.02 degree cells (about 2 km)
PROJ_CRS = os.environ.get("REGION_CRS", "EPSG:32643")   # UTM zone 43N, metres, fits Delhi NCR

# OpenStreetMap download (Geofabrik). Only the zones that touch the circle are downloaded.
GEOFABRIK = "https://download.geofabrik.de/asia/india/"
ZONES = ["northern-zone", "central-zone", "western-zone", "eastern-zone"]

REGION_DIR = os.path.join("data", "region")
OSM_DIR = os.path.join(REGION_DIR, "osm")
WORK_DIR = os.path.join(REGION_DIR, "work")
DB_PATH = os.path.join(REGION_DIR, "region.db")
BASEMAP_PATH = os.path.join(REGION_DIR, "basemap.mbtiles")


def slot_dates():
    """The date that stands for each week: the middle day of that week."""
    import datetime as dt
    first = dt.date(REF_YEAR, 1, 1)
    return [first + dt.timedelta(days=7 * w + 3) for w in range(WEEKS)]
