# Offline shade data: 40 km around Chhatarpur

This builds shade for every walkable street within 40 km of Chhatarpur (all of Delhi,
Gurugram, Noida, Faridabad and most of Ghaziabad). It covers every week of the year and
every hour from 6 AM to 7 PM, and saves it on your laptop. Once it's built, the app works
with no internet: map, street shade, shady routes and place search. Only live weather
needs internet.

The shade maths matches your `shade_lib.py`: 8 m corridor, building heights from OSM,
shadows capped at 300 m, satellite canopy from 3 m, mapped trees as 4 m circles, and full
shade after sunset. On a test area the new builder agreed with your current method to
within about 3 percentage points (correlation 0.97).

## 1. Copy the files into your project

```
shade-routes/
  build_region.py          (new)  builds the data
  region_config.py         (new)  centre, radius, settings
  offline_map.py           (new)  saves the base map for offline use
  shade_lib.py, areas.py   (yours, unchanged; the builder reuses them)
  backend/
    region_engine.py       (new)  shade lookup + shady routing from the saved data
    region_api.py          (new)  the API endpoints
    static/index.html      (new)  the app page (served at /app)
    main.py, engine.py     (yours)
```

Add these two lines to `backend/main.py`, after `app = FastAPI(...)`:

```python
from region_api import router as region_router
app.include_router(region_router)
```

Add this line to `.gitignore`, because the data is large:

```
data/region/
```

## 2. Build the data (once, while online)

In VS Code's terminal, from the `shade-routes` folder with your venv active:

```
pip install osmium numba scipy requests
python build_region.py
python offline_map.py
```

| Step | What it does | Time on a 16 GB PC |
|---|---|---|
| download | Downloads OpenStreetMap for the Geofabrik zones that touch the circle (200–550 MB) | depends on your internet |
| osm | Reads streets, buildings, trees and places | 10–30 min |
| prepare | Splits the circle into 2 km tiles | 5–10 min |
| shade | Computes 728 time slots per street, using all CPU cores. The first run also downloads satellite canopy | 1–3 hours the first time |
| pack | Writes `data/region/region.db` (about 400–700 MB) | a few min |
| offline_map.py | Saves about 2,000 map tiles and the label fonts (100–200 MB) | 10–20 min |

You can press Ctrl+C at any point and run the same command again; it continues where it
stopped. If some tiles couldn't get the satellite canopy (for example, your Wi‑Fi dropped),
it tells you so. Run `python build_region.py --redo-no-canopy` later to fill them in.

Useful options:
- `--workers 6` uses fewer CPU cores, so your laptop stays usable.
- `--no-canopy` skips the satellite trees. It's faster, but shows less shade.
- `--step shade` runs one step again.

## 3. Run it

```
cd backend
uvicorn main:app --reload
```

Open **http://localhost:8000/app**. A label at the top of the map says "Offline data ·
Delhi NCR". Turn on the shade layer and zoom in to street level to see the colours.
Directions inside the circle use your saved data, with the fastest and the coolest route
and their real shade %. Search finds saved places even when you're offline.

## On your phone (same Wi‑Fi as the laptop)

```
uvicorn main:app --host 0.0.0.0 --port 8000
```

On the phone, open `http://<your laptop's IP>:8000/app`. Find the IP with `ipconfig`; it's
the IPv4 Address, for example `192.168.1.5`. The map, shade, routes and search all work.
**Phones block the location button on plain `http://` addresses**, so search for a starting
point or tap the map instead. A standalone phone pack, with no laptop needed, can come next.

## Change the circle

Edit `CENTER` and `RADIUS_M` in `region_config.py`. Then delete `data/region/work` and run
`python build_region.py` again.

## API (for your own frontend)

- `GET /api/region/shade?west=&south=&east=&north=&when=2026-05-15T14:30`: street pieces as GeoJSON, each with `shade` from 0 to 1
- `GET /api/region/route?from_lat=&from_lon=&to_lat=&to_lon=&when=`: fastest and shadiest routes, in the same format as `engine.route`
- `GET /api/region/places?q=metro&lat=&lon=`: offline place search
- `GET /api/region/street/{id}?date=2026-05-15`: one street's shade hour by hour for one day
- `GET /api/region/info`: what was built, and when

`when` is Delhi time. Leave it out to mean now. Any date of the year works, because the
backend blends the two nearest weeks and the two nearest hours.
