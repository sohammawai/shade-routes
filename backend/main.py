"""Shade Routes API (Phase 7).

Run on your laptop, from the backend folder:
    uvicorn main:app --reload
Then open http://127.0.0.1:8000/docs to try every endpoint.

On AWS Lambda, 'handler' below is the entry point (Phase 9).
"""
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field

import engine

app = FastAPI(title="Shade Routes API", version="1.0",
              description="Where to plant trees, and the shadiest way to walk.")


def area_or_404(key):
    try:
        return engine.get_area(key)
    except KeyError:
        raise HTTPException(404, f"Unknown area '{key}'")


def street_or_404(area, street_id):
    if street_id not in area.streets:
        raise HTTPException(404, f"Unknown street '{street_id}' in {area.name}")
    return street_id


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.get("/api/areas")
def areas():
    return engine.list_areas()


@app.get("/api/compare")
def compare():
    return engine.compare_areas()


@app.get("/api/areas/{key}")
def area_info(key: str):
    return area_or_404(key).info()


@app.get("/api/areas/{key}/streets")
def streets(key: str):
    """Every street segment as GeoJSON, with priority score and hourly shade."""
    return area_or_404(key).streets_geojson()


@app.get("/api/areas/{key}/priority")
def priority(key: str, limit: int = Query(10, ge=1, le=50), hour: int = Query(14, ge=0, le=23)):
    return area_or_404(key).top_streets(limit, hour)


@app.get("/api/areas/{key}/streets/{street_id}")
def street(key: str, street_id: str):
    area = area_or_404(key)
    return area.street_detail(street_or_404(area, street_id))


class SimulateRequest(BaseModel):
    street_id: str
    trees: int = Field(24, ge=0, le=1000)
    hour: int = Field(14, ge=0, le=23)


@app.post("/api/areas/{key}/simulate")
def simulate(key: str, req: SimulateRequest):
    area = area_or_404(key)
    return area.simulate(street_or_404(area, req.street_id), req.trees, req.hour)


@app.get("/api/areas/{key}/route")
def route(key: str, from_lat: float, from_lon: float, to_lat: float, to_lon: float,
          hour: int = Query(14, ge=0, le=23)):
    area = area_or_404(key)
    try:
        return area.route(from_lat, from_lon, to_lat, to_lon, hour)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.get("/api/areas/{key}/places")
def places(key: str, q: str = ""):
    return area_or_404(key).find_places(q)


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=1000)
    area: str = "connaught_place"
    history: list[dict] = []


@app.post("/api/chat")
def chat(req: ChatRequest):
    area_or_404(req.area)
    try:
        import chat as chatbot                     # Phase 8; needs strands-agents and Bedrock
        return chatbot.answer(req.message, req.area, req.history)
    except Exception as e:                         # the map keeps working if the chatbot can't
        print(f"Chat error: {type(e).__name__}: {e}")
        return {"answer": "Sorry, the assistant is unavailable right now. "
                          "The map, planner and routes still work.",
                "actions": {}, "error": type(e).__name__}


try:                                               # AWS Lambda entry point (Phase 9)
    from mangum import Mangum
    handler = Mangum(app, lifespan="off")
except ImportError:
    handler = None
