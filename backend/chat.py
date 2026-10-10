"""The Shade Routes assistant (Phase 8), built with the Strands Agents SDK (AWS open source).

The agent answers in plain language but takes every number from the same engine
the website uses, through tools. On AWS it runs Amazon Nova 2 Lite on Bedrock.
"""
import json
import os

from strands import Agent, tool
from strands.models import BedrockModel

import engine

MODEL_ID = os.environ.get("CHAT_MODEL_ID", "global.amazon.nova-2-lite-v1:0")
REGION = os.environ.get("CHAT_REGION") or os.environ.get("AWS_REGION") or "ap-southeast-2"

SYSTEM_PROMPT = """You are the assistant inside Shade Routes, a tree planting planner and shady
walking router for Delhi. The user is looking at {area} (area key "{key}").
All areas: {others}.

Rules:
- Use the tools for every number, street, place and route. Never guess numbers.
- Answer in 2 to 4 short sentences of plain language. Use metres, minutes and times like 2 PM.
- The numbers come from simulated walking trips and satellite tree data: call them estimates.
- For a question about another area, pass that area's key to the tool.
- If a street or place is not found, say so and suggest one that exists.
- Only help with shade, trees, heat, walking routes and these areas."""


def make_model():
    """The language model. Tests replace this with a stand-in."""
    return BedrockModel(model_id=MODEL_ID, region_name=REGION, temperature=0.2, max_tokens=700)


def _history_messages(history):
    """The last few chat turns, in the alternating user/assistant shape the model expects."""
    msgs = []
    for h in (history or [])[-8:]:
        role, text = h.get("role"), str(h.get("text", "")).strip()
        if role not in ("user", "assistant") or not text:
            continue
        if msgs and msgs[-1]["role"] == role:
            msgs[-1]["content"][0]["text"] += "\n" + text
        else:
            msgs.append({"role": role, "content": [{"text": text}]})
    while msgs and msgs[0]["role"] != "user":
        msgs.pop(0)
    if msgs and msgs[-1]["role"] == "user":       # the new question is added by the agent call
        msgs.pop()
    return msgs


def answer(message, area_key, history=None):
    current = engine.get_area(area_key)
    actions = {"area": area_key, "highlight": [], "route": None}

    def area_for(key):
        try:
            return engine.get_area(key or area_key)
        except KeyError:
            raise ValueError(f"Unknown area '{key}'. Areas: {', '.join(a['key'] for a in engine.list_areas())}")

    @tool
    def top_streets(count: int = 5, hour: int = 14, area_key: str = area_key) -> str:
        """Streets where new trees would help the most walkers, best first.

        Args:
            count: how many streets to return, 1 to 10
            hour: hour of day for the shade figure, 8 to 18
            area_key: which area, for example connaught_place
        """
        area = area_for(area_key)
        rows = area.top_streets(max(1, min(int(count), 10)), hour)
        actions.update(area=area.key, highlight=[r["id"] for r in rows[:3]])
        return json.dumps({"area": area.name, "streets": rows})

    @tool
    def street_info(street_name: str, area_key: str = area_key) -> str:
        """Details for one street: length, daily walking trips, priority and shade by hour.

        Args:
            street_name: the street's name, for example Janpath
            area_key: which area the street is in
        """
        area = area_for(area_key)
        s = area.find_street(street_name)
        if not s:
            return json.dumps({"error": f"No street like '{street_name}' in {area.name}"})
        d = area.street_detail(s["id"])
        actions.update(area=area.key, highlight=[s["id"]])
        hourly = {engine.hour_label(h): f"{round(100 * v)}%" for h, v in zip(d["hours"], d["shade"])}
        return json.dumps({k: d[k] for k in ("name", "rank", "score", "length_m", "trips_per_day",
                                             "sun_hot_pct", "max_trees")} | {"shade_by_hour": hourly})

    @tool
    def simulate_planting(street_name: str, trees: int, hour: int = 14, area_key: str = area_key) -> str:
        """What planting new trees along a street would do to its shade.

        Args:
            street_name: the street's name
            trees: how many new trees to plant
            hour: hour of day to report shade for, 8 to 18
            area_key: which area the street is in
        """
        area = area_for(area_key)
        s = area.find_street(street_name)
        if not s:
            return json.dumps({"error": f"No street like '{street_name}' in {area.name}"})
        r = area.simulate(s["id"], trees, hour)
        actions.update(area=area.key, highlight=[s["id"]])
        return json.dumps({k: r[k] for k in ("name", "trees", "max_trees", "hour", "shade_before_pct",
                                             "shade_after_pct", "walkers_per_day", "shaded_km_per_day",
                                             "assumptions")})

    @tool
    def find_place(query: str, area_key: str = area_key) -> str:
        """Search named places such as metro gates, bus stops, markets and colleges.

        Args:
            query: part of the place name, for example Rajiv Chowk
            area_key: which area to search
        """
        area = area_for(area_key)
        return json.dumps(area.find_places(query, limit=5))

    @tool
    def shadiest_route(from_place: str, to_place: str, hour: int = 14, area_key: str = area_key) -> str:
        """Compare the fastest and the shadiest walking route between two named places.

        Args:
            from_place: start place name, for example Rajiv Chowk Gate 7
            to_place: end place name, for example Janpath Market
            hour: hour of day, 8 to 18
            area_key: which area both places are in
        """
        area = area_for(area_key)
        a, b = area.find_places(from_place, 1), area.find_places(to_place, 1)
        if not a or not b:
            missing = from_place if not a else to_place
            return json.dumps({"error": f"No place like '{missing}' in {area.name}"})
        a, b = a[0], b[0]
        try:
            r = area.route(a["lat"], a["lon"], b["lat"], b["lon"], hour)
        except ValueError as e:
            return json.dumps({"error": str(e)})
        actions.update(area=area.key, route={"from": a, "to": b, "hour": r["hour"]})
        trim = {k: v for k, v in r.items() if k not in ("fastest", "shadiest")}
        for k in ("fastest", "shadiest"):
            trim[k] = {x: r[k][x] for x in ("length_m", "minutes", "shade_pct")}
        return json.dumps({"from": a["name"], "to": b["name"], **trim})

    @tool
    def compare_areas(hour: int = 12) -> str:
        """Average street shade in every area at one hour of the day.

        Args:
            hour: hour of day, 8 to 18
        """
        rows = []
        for a in engine.compare_areas():
            i = a["hours"].index(min(max(int(hour), a["hours"][0]), a["hours"][-1]))
            rows.append({"area": a["name"], "key": a["key"], "shade_pct": round(100 * a["hourly_shade"][i])})
        return json.dumps(sorted(rows, key=lambda r: r["shade_pct"]))

    others = ", ".join(f"{a['name']} ({a['key']})" for a in engine.list_areas())
    agent = Agent(
        model=make_model(),
        tools=[top_streets, street_info, simulate_planting, find_place, shadiest_route, compare_areas],
        system_prompt=SYSTEM_PROMPT.format(area=current.name, key=area_key, others=others),
        messages=_history_messages(history),
        callback_handler=None,                    # no printing; we return the answer instead
    )
    result = agent(message)
    return {"answer": str(result).strip(), "actions": actions}
