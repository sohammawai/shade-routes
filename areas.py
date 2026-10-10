"""The areas Shade Routes knows about.

To add an area, add one entry to AREAS. The key is a short id used for folder
names and web addresses: lowercase letters, numbers and underscores only.
Check each centre by pasting "latitude, longitude" into Google Maps.
"""

AREAS = {
    "connaught_place": {
        "name": "Connaught Place",
        "center": (28.6315, 77.2167),   # (latitude, longitude)
        "radius": 800,                    # metres around the centre
    },
    "chandni_chowk": {
        "name": "Chandni Chowk",
        "center": (28.6562, 77.2305),
        "radius": 700,
    },
    "lajpat_nagar": {
        "name": "Lajpat Nagar Central Market",
        "center": (28.5677, 77.2433),
        "radius": 700,
    },
    "nehru_place": {
        "name": "Nehru Place",
        "center": (28.5494, 77.2519),
        "radius": 700,
    },
    "du_north_campus": {
        "name": "Delhi University North Campus",
        "center": (28.6880, 77.2105),
        "radius": 800,
    },
}

DEFAULT_AREA = "connaught_place"

# One fixed hot day for every area, so results compare fairly
DATE = "2026-05-15"
HOURS = list(range(8, 19))          # 8am to 6pm
HOT_HOURS = [12, 13, 14, 15, 16]    # the hours that matter most for heat
