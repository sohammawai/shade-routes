"""Shade Assistant: a real chatbot on Amazon Bedrock that answers from the saved shade data.

It uses Bedrock's Converse API with tools, so the model looks things up instead of guessing:
search places, find the nearest cafés/metro/parks, plan the shadiest walking route, check
shade on a street through the day, and list where trees are needed most.

Needs bedrock:InvokeModel permission (PowerUserAccess has it). The model can be chosen with
the BEDROCK_MODEL_ID environment variable; otherwise the first one that works is used.
"""
import datetime as dt
import json
import os
import re
import time

from region_engine import CAT_CLASSES, IST, get_region, parse_when

# Sydney (ap-southeast-2) reaches Nova 2 Lite only through the "global." profile, and Claude Haiku 4.5
# through "au." or "global.". Older models (Nova 1, Claude 3) have reached end of life on Bedrock.
MODELS = [m for m in [os.environ.get("BEDROCK_MODEL_ID")] if m] + [
    "global.amazon.nova-2-lite-v1:0",
    "au.anthropic.claude-haiku-4-5-20251001-v1:0", "global.anthropic.claude-haiku-4-5-20251001-v1:0",
    "apac.amazon.nova-lite-v1:0", "apac.amazon.nova-pro-v1:0",
]
MODELS = list(dict.fromkeys(MODELS))               # no repeats if BEDROCK_MODEL_ID is one of these
PREFER = ["nova-2-lite", "haiku-4-5", "nova-2", "haiku", "nova-pro", "nova-lite", "sonnet"]


def _discover(tried):
    """If every known model fails, ask Bedrock which inference profiles exist here and try the best ones."""
    try:
        import boto3
        out = boto3.client("bedrock").list_inference_profiles(typeEquals="SYSTEM_DEFINED", maxResults=1000)
        ids = [p["inferenceProfileId"] for p in out.get("inferenceProfileSummaries", []) if p.get("status", "ACTIVE") == "ACTIVE"]
    except Exception as e:
        print(f"Chat: couldn't list Bedrock models: {type(e).__name__}: {e}")
        return []
    rank = lambda i: next((k for k, w in enumerate(PREFER) if w in i), None)
    good = [i for i in ids if i not in tried and rank(i) is not None and ("amazon." in i or "anthropic." in i)]
    return sorted(good, key=lambda i: (rank(i), not i.startswith(("au.", "apac.")), i))[:6]


_working_model = None
_resting = {}                 # model -> time it may be tried again (after hitting its quota)
MAX_TOOL_ROUNDS = 4

TOOLS = [
    {"name": "search_places", "description": "Find places by name in Delhi NCR (metro stations, markets, parks, monuments, cafés, malls). Returns names with coordinates.",
     "schema": {"type": "object", "properties": {"query": {"type": "string", "description": "Place name, e.g. 'Hauz Khas metro' or 'Select Citywalk'"}},
                "required": ["query"]}},
    {"name": "nearby_places", "description": "The nearest places of one kind to a point, nearest first.",
     "schema": {"type": "object", "properties": {
         "category": {"type": "string", "enum": sorted(CAT_CLASSES), "description": "park, cafe, food (restaurants), transit (metro/rail), shop, sights, health"},
         "lat": {"type": "number"}, "lon": {"type": "number"}, "limit": {"type": "integer"}},
         "required": ["category", "lat", "lon"]}},
    {"name": "shady_route", "description": "Walking route between two points: the fastest one and the shadiest one (at most 30% longer), with minutes and % of the walk in shade.",
     "schema": {"type": "object", "properties": {
         "from_lat": {"type": "number"}, "from_lon": {"type": "number"}, "from_name": {"type": "string"},
         "to_lat": {"type": "number"}, "to_lon": {"type": "number"}, "to_name": {"type": "string"},
         "when": {"type": "string", "description": "Optional Delhi time, ISO like 2026-05-15T14:30. Leave out for now."}},
         "required": ["from_lat", "from_lon", "to_lat", "to_lon"]}},
    {"name": "shade_at", "description": "Shade on the street nearest a point, now and for every hour of that day, plus the shadiest daytime hour.",
     "schema": {"type": "object", "properties": {"lat": {"type": "number"}, "lon": {"type": "number"},
                                                 "when": {"type": "string", "description": "Optional Delhi date/time ISO"}},
                "required": ["lat", "lon"]}},
    {"name": "plant_priorities", "description": "Streets around a point (about 1.5 km) where new trees would help walkers most at 2 PM, with how many trees each needs.",
     "schema": {"type": "object", "properties": {"lat": {"type": "number"}, "lon": {"type": "number"}},
                "required": ["lat", "lon"]}},
]


def _run_tool(name, a, actions):
    r = get_region()
    if name == "search_places":
        out = r.places(a["query"], a.get("near_lat"), a.get("near_lon"), 4)
        return [{k: p[k] for k in ("name", "class", "lat", "lon")} for p in out] or {"result": "No saved place matches that name."}
    if name == "nearby_places":
        res = r.nearby(a["lat"], a["lon"], [a["category"]], min(int(a.get("limit", 4)), 6))
        return res.get(a["category"], {"items": []})["items"]
    if name == "shady_route":
        try:
            res = r.route(a["from_lat"], a["from_lon"], a["to_lat"], a["to_lon"], a.get("when"))
        except ValueError as e:
            return {"error": str(e)}
        actions.append({"type": "route", "when": a.get("when"),
                        "from": {"name": a.get("from_name") or "Start", "lat": a["from_lat"], "lng": a["from_lon"]},
                        "to": {"name": a.get("to_name") or "Destination", "lat": a["to_lat"], "lng": a["to_lon"]}})
        brief = lambda x: {k: x[k] for k in ("length_m", "minutes", "shade_pct", "sun_m")}
        return {"when": res["when"], "night": res["night"], "summary": res["summary"],
                "fastest": brief(res["fastest"]), "shadiest": brief(res["shadiest"])}
    if name == "shade_at":
        return r.shade_at(a["lat"], a["lon"], a.get("when")) or {"error": "No street within 300 m of that point."}
    if name == "plant_priorities":
        d = 0.012
        when = parse_when(None).replace(hour=14, minute=0).isoformat()
        p = r.planner(a["lon"] - d, a["lat"] - d, a["lon"] + d, a["lat"] + d, when, 5)
        actions.append({"type": "planner", "lat": a["lat"], "lng": a["lon"]})
        p["rows"] = [{k: row[k] for k in ("name", "length_m", "shade_pct", "trees", "shade_after_pct")} for row in p["rows"]]
        return p
    return {"error": f"Unknown tool {name}"}


# ---------- built-in answers, used when no Bedrock model is available (daily limit, no access, offline laptop) ----------
CAT_WORDS = [("cafe", r"caf[eé]|coffee|chai|\btea\b|bakery"), ("food", r"restaurant|food|eat|dinner|lunch|breakfast|dhaba|biryani"),
             ("transit", r"metro|station|train|rail|\bbus\b"), ("park", r"park|garden|green"), ("shop", r"shop|mall|market|store"),
             ("health", r"hospital|clinic|pharmac|chemist|medical|doctor"), ("sights", r"temple|mosque|church|gurudwara|monument|museum|sight")]
CAT_NAME = {"cafe": "café", "food": "restaurant", "transit": "metro or rail station", "park": "park", "shop": "shop",
            "health": "hospital or clinic", "sights": "sight"}


def sun_day(day, lat, lon):
    """Sunrise, sunset (Delhi time) and the sun's highest angle on a day (NOAA's simple formula, within a minute or two)."""
    import math
    g = 2 * math.pi / 365 * (day.timetuple().tm_yday - 1)
    eq = 229.18 * (0.000075 + 0.001868 * math.cos(g) - 0.032077 * math.sin(g) - 0.014615 * math.cos(2 * g) - 0.040849 * math.sin(2 * g))
    dec = (0.006918 - 0.399912 * math.cos(g) + 0.070257 * math.sin(g) - 0.006758 * math.cos(2 * g) + 0.000907 * math.sin(2 * g)
           - 0.002697 * math.cos(3 * g) + 0.00148 * math.sin(3 * g))
    la = math.radians(lat)
    ha = math.degrees(math.acos(math.cos(math.radians(90.833)) / (math.cos(la) * math.cos(dec)) - math.tan(la) * math.tan(dec)))
    at = lambda mins: (dt.datetime(day.year, day.month, day.day, tzinfo=IST) + dt.timedelta(minutes=mins + 330)).strftime("%I:%M %p").lstrip("0")
    return {"rise": at(720 - 4 * (lon + ha) - eq), "set": at(720 - 4 * (lon - ha) - eq), "noon": at(720 - 4 * lon - eq),
            "max_alt": round(90 - abs(lat - math.degrees(dec)))}


def _fmt_m(m):
    return f"{m / 1000:.1f} km" if m >= 1000 else f"{round(m / 10) * 10:.0f} m"


def _route_text(r, lat, lon, to, when, actions):
    try:
        res = r.route(lat, lon, to["lat"], to["lon"], when)
    except ValueError as e:
        return str(e)
    actions.append({"type": "route", "when": when, "from": {"name": "Your location", "lat": lat, "lng": lon},
                    "to": {"name": to["name"], "lat": to["lat"], "lng": to["lon"]}})
    f, s = res["fastest"], res["shadiest"]
    if res["night"]:
        return f"It's {f['minutes']} min on foot ({_fmt_m(f['length_m'])}). The sun is down, so the whole way is shaded. It's on the map."
    if s is f or s["length_m"] == f["length_m"]:
        return f"It's {f['minutes']} min on foot ({_fmt_m(f['length_m'])}), {f['shade_pct']}% in shade, and that is already the shadiest way. It's on the map."
    return (f"The shadiest way is {s['minutes']} min ({_fmt_m(s['length_m'])}) with {s['shade_pct']}% in shade; "
            f"the fastest is {f['minutes']} min with {f['shade_pct']}%. Both are on the map.")


def local_answer(message, lat=None, lon=None, when=None):
    r = get_region()
    m, actions = message.lower(), []
    if lat is None or lon is None:
        lat, lon = r.meta["center"][0], r.meta["center"][1]
    t = parse_when(when)
    if "tomorrow" in m:
        t = t + dt.timedelta(days=1)
        when = t.isoformat()
    day_word = "Tomorrow" if "tomorrow" in m else "Today" if t.date() == dt.datetime.now(IST).date() else t.strftime("On %a %d %b")
    hi = re.match(r"\s*(hi|hello|hey|namaste|good (morning|afternoon|evening))\b", m)
    name = re.search(r"\b(?:i am|i'm|i m|im|my name is|this is)\s+([a-z]+)", m)
    cat = next((c for c, rx in CAT_WORDS if re.search(rx, m)), None)
    wants_route = re.search(r"route|way|walk|go to|get to|take me|directions|reach|how do i get", m)
    if re.search(r"tree|plant|planner|greener|heat island", m):
        d = 0.012
        p = r.planner(lon - d, lat - d, lon + d, lat + d, parse_when(when).replace(hour=14, minute=0).isoformat(), 3)
        actions.append({"type": "planner", "lat": lat, "lng": lon})
        if not p["rows"]:
            return "I couldn't find walking streets close to you to plan.", actions
        rows = "; ".join(f"{x['name']} ({x['length_m']} m, {x['shade_pct']}% shade" + (f", {x['trees']} trees" if x["trees"] else "") + ")"
                         for x in p["rows"])
        return f"Around you, streets average {p['avg_shade_pct']}% shade at 2 PM. Plant first on: {rows}. The Planner shows the rest.", actions
    if cat:
        items = r.nearby(lat, lon, [cat], 3).get(cat, {"items": []})["items"]
        if not items:
            return f"I couldn't find a {CAT_NAME[cat]} in the saved area.", actions
        first = items[0]
        if wants_route or "nearest" in m or "closest" in m:
            return f"The nearest {CAT_NAME[cat]} is {first['name']}, {_fmt_m(first['dist_m'])} away. " + _route_text(r, lat, lon, first, when, actions), actions
        return f"Nearest {CAT_NAME[cat]}s: " + ", ".join(f"{x['name']} ({_fmt_m(x['dist_m'])})" for x in items) + ". Ask for a route to any of them.", actions
    mt = re.search(r"(?:route|way|walk|directions|go|get|take me)\s+(?:me\s+)?(?:to|till|until)\s+(.+)", message, re.I)
    if mt:
        q = re.sub(r"[?.!]+$", "", mt.group(1)).strip()
        hits = r.places(q, lat, lon, 1) if q else []
        if not hits:
            return f"I couldn't find \"{q}\" in the saved places. Try another name.", actions
        h = hits[0]
        return f"{h['name']}: " + _route_text(r, lat, lon, h, when, actions), actions
    if re.search(r"shad|sun|hot|heat|cool|best time|when|weather|daylight", m):
        sd, sa = sun_day(t.date(), lat, lon), r.shade_at(lat, lon, when)
        sun = (f"{day_word} the sun rises at {sd['rise']} and sets at {sd['set']}, highest ({sd['max_alt']}°) around {sd['noon']}.")
        if not sa:
            return sun, actions
        hours = sa["by_hour_pct"]
        mid = hours.get("13:00")
        street = (f" On {sa['street']} the coolest daytime hour is {sa['shadiest_daytime_hour']}"
                  + (f" ({hours.get(sa['shadiest_daytime_hour'])}% shade vs {mid}% at 1 PM)." if mid is not None and sa['shadiest_daytime_hour'] else "."))
        now = "" if "tomorrow" in m or sa["night"] else f" Right now it's {sa['shade_now_pct']}% shaded."
        return sun + street + now, actions
    if hi or name:
        who = f" {name.group(1).capitalize()}" if name else ""
        return (f"Hi{who}! I can find the shadiest walk to a place, tell you when a street is coolest, "
                "or show where trees are needed most. Try \"shady route to the nearest café\"."), actions
    return ("I can find the shadiest walk to a place (\"shady route to the nearest metro\", \"walk to Qutub Minar\"), "
            "tell you when a street is coolest (\"when is it shadiest here?\"), or show where trees are needed most "
            "(\"where should trees go?\")."), actions


def _local(message, lat, lon, when, note):
    try:
        text, actions = local_answer(message, lat, lon, when)
    except Exception as e:
        print(f"Chat local answer failed: {type(e).__name__}: {e}")
        text, actions = "Sorry, I couldn't work that out.", []
    return {"answer": text + (f"\n\n({note})" if note else ""), "actions": actions, "model": "built-in"}


def _system(lat, lon, when):
    t = parse_when(when)
    r = get_region()
    return (
        "You are Shade Assistant, the helper inside Shade Routes, an app that shows how shaded every street in "
        f"{r.meta['name']} is at any time of year, and finds the coolest walking routes. "
        f"It is now {t.strftime('%A %d %B %Y, %I:%M %p')} in Delhi. "
        + (f"The user is at or looking at lat {lat:.5f}, lon {lon:.5f}; use this for 'here', 'near me' and 'from my location'. " if lat is not None else "")
        + "Always use the tools for facts about places, routes, shade and trees; never invent numbers or places. "
        "To route between named places, first find their coordinates with search_places or nearby_places. "
        "Answer in 1-4 short sentences in plain text (no markdown, no lists unless asked), with the key numbers "
        "(minutes, % shade, distance). Shade comes from buildings and trees; after sunset everything counts as shade. "
        "Paths with no name are called by their kind and an ID (a Plus Code), like 'Lane G5FJ+2V'; always quote that ID exactly. "
        "If asked something unrelated to walking, shade, heat, trees or places in Delhi NCR, answer briefly and steer back."
    )


def answer(message, history=None, lat=None, lon=None, when=None):
    """One chat turn. Returns {"answer": text, "actions": [...]} (actions tell the app what to show)."""
    global _working_model
    try:
        import boto3
        from botocore.config import Config
    except ImportError:
        boto3 = None
    if get_region() is None:
        return {"answer": "The shade data isn't loaded on the server yet.", "actions": []}
    if boto3 is None:
        return _local(message, lat, lon, when, "Quick answer from the built-in tools; the AI runs on the deployed app.")
    client = boto3.client("bedrock-runtime", config=Config(read_timeout=30, retries={"max_attempts": 1, "mode": "standard"}))
    msgs = []
    for h in (history or [])[-6:]:
        role = "assistant" if h.get("role") == "assistant" else "user"
        text = str(h.get("text", ""))[:600]
        if role == "assistant":                            # leave out error replies and the "quick answer" note
            text = "" if "can't reach Amazon Bedrock" in text else text.split("\n\n(Quick answer")[0]
        if text and (not msgs or msgs[-1]["role"] != role):
            msgs.append({"role": role, "content": [{"text": text}]})
    while msgs and msgs[0]["role"] != "user":
        msgs.pop(0)
    if msgs and msgs[-1]["role"] == "user":
        msgs.pop()
    msgs.append({"role": "user", "content": [{"text": message[:1500]}]})
    tool_config = {"tools": [{"toolSpec": {"name": t["name"], "description": t["description"],
                                           "inputSchema": {"json": t["schema"]}}} for t in TOOLS]}
    actions, last_error, tried = [], None, []
    models = [m for m in ([_working_model] if _working_model else []) + [m for m in MODELS if m != _working_model]
              if _resting.get(m, 0) < time.time()]
    i = 0
    while i < len(models):
        model = models[i]
        i += 1
        tried.append(model)
        try:
            convo = list(msgs)
            for _ in range(MAX_TOOL_ROUNDS + 1):
                resp = client.converse(modelId=model, messages=convo, system=[{"text": _system(lat, lon, when)}],
                                       toolConfig=tool_config, inferenceConfig={"maxTokens": 400, "temperature": 0.2})
                _working_model = model
                out = resp["output"]["message"]
                convo.append(out)
                uses = [c["toolUse"] for c in out["content"] if "toolUse" in c]
                if resp.get("stopReason") != "tool_use" or not uses:
                    text = " ".join(c["text"] for c in out["content"] if "text" in c).strip()
                    return {"answer": text or "Sorry, I couldn't work that out.", "actions": actions, "model": model}
                results = []
                for u in uses:
                    try:
                        res = _run_tool(u["name"], u.get("input") or {}, actions)
                    except Exception as e:                     # a bad tool call shouldn't end the chat
                        res = {"error": f"{type(e).__name__}: {e}"}
                    results.append({"toolResult": {"toolUseId": u["toolUseId"],
                                                   "content": [{"text": json.dumps(res, default=str)[:2500]}]}})
                convo.append({"role": "user", "content": results})
            return {"answer": "That took too many steps. Try asking a simpler question.", "actions": actions}
        except Exception as e:
            last_error = e
            code = getattr(e, "response", {}).get("Error", {}).get("Code", "")
            print(f"Chat: {model} failed: {code or type(e).__name__}: {e}")
            if code in ("ThrottlingException", "ServiceQuotaExceededException", "TooManyRequestsException"):
                _resting[model] = time.time() + (3600 if "per day" in str(e) else 60)   # its quota is used up: rest it
                if _working_model == model:
                    _working_model = None
                continue                                       # another model has its own quota
            if code in ("AccessDeniedException", "ValidationException", "ResourceNotFoundException") or "model" in str(e).lower():
                _working_model = None
                if i == len(models) and all(m in MODELS for m in models):
                    models += _discover(tried)                 # last resort: whatever this region offers
                continue                                       # try the next model
            break
    print(f"Chat error: {type(last_error).__name__ if last_error else 'none'}: {last_error}  (tried: {', '.join(tried) or 'none, all resting'})")
    limit = last_error is None or "Throttling" in type(last_error).__name__ or "per day" in str(last_error) \
        or "Throttling" in str(getattr(last_error, "response", {}).get("Error", {}).get("Code", ""))
    return _local(message, lat, lon, when, "Quick answer from the built-in tools: the AI model has hit its daily limit."
                  if limit else "Quick answer from the built-in tools: the AI model isn't reachable right now.")
