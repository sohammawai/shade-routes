import { useCallback, useEffect, useRef, useState } from "react";
import { api, API_BASE } from "./api.js";
import TopBar from "./components/TopBar.jsx";
import MapView from "./components/MapView.jsx";
import PlannerPanel from "./components/PlannerPanel.jsx";
import StreetPanel from "./components/StreetPanel.jsx";
import RoutesPanel from "./components/RoutesPanel.jsx";
import ChatPanel from "./components/ChatPanel.jsx";
import CompareModal from "./components/CompareModal.jsx";

const NO_ENDS = { from: null, to: null };

export default function App() {
  const [areas, setAreas] = useState([]);
  const [areaKey, setAreaKey] = useState(null);
  const [mode, setMode] = useState("planner");        // "planner" or "routes"
  const [hour, setHour] = useState(14);
  const [streets, setStreets] = useState(null);       // GeoJSON of the current area
  const [ranking, setRanking] = useState([]);
  const [selectedId, setSelectedId] = useState(null); // street open in the detail panel
  const [highlight, setHighlight] = useState([]);     // streets the chatbot pointed at
  const [focus, setFocus] = useState(null);           // map bounds to zoom to
  const [ends, setEnds] = useState(NO_ENDS);          // route start and end
  const [route, setRoute] = useState(null);
  const [showCompare, setShowCompare] = useState(false);
  const [error, setError] = useState("");
  const pending = useRef(null);                       // chatbot actions waiting for an area switch

  const area = areas.find((a) => a.key === areaKey);

  // Chatbot answers can move the map: highlight streets, or draw a route
  const applyActions = useCallback((actions) => {
    if (actions.route) {
      setMode("routes");
      setEnds({ from: actions.route.from, to: actions.route.to });
      setHour(actions.route.hour);
    } else if (actions.highlight?.length) {
      setMode("planner");
      setHighlight(actions.highlight);
      setSelectedId(actions.highlight[0]);
    }
  }, []);

  const changeArea = (key) => {
    setEnds(NO_ENDS);                                 // old points belong to the old area
    setRoute(null);
    setAreaKey(key);
  };

  const handleChatActions = (actions) => {
    if (actions.area && actions.area !== areaKey) {
      pending.current = actions;                      // apply after the new area loads
      changeArea(actions.area);
    } else {
      applyActions(actions);
    }
  };

  useEffect(() => {
    api.areas()
      .then((list) => {
        setAreas(list);
        setAreaKey(list[0]?.key ?? null);
      })
      .catch((e) => setError(`Can't reach the Shade Routes API at ${API_BASE || "/api"} (${e.message}).`));
  }, []);

  useEffect(() => {
    if (!areaKey) return;
    setStreets(null);
    setSelectedId(null);
    setHighlight([]);
    setEnds(NO_ENDS);
    setRoute(null);
    setFocus(null);
    const p = pending.current;
    pending.current = null;
    if (p && p.area === areaKey) applyActions(p);
    api.streets(areaKey).then(setStreets).catch((e) => setError(e.message));
  }, [areaKey, applyActions]);

  useEffect(() => {
    if (areaKey) api.priority(areaKey, hour).then(setRanking).catch((e) => setError(e.message));
  }, [areaKey, hour]);

  useEffect(() => {
    if (!areaKey || !ends.from || !ends.to) {
      setRoute(null);
      return;
    }
    let live = true;
    api.route(areaKey, ends.from, ends.to, hour)
      .then((r) => live && setRoute(r))
      .catch((e) => live && setRoute({ error: e.message }));
    return () => {
      live = false;
    };
  }, [areaKey, ends, hour]);

  const handleMapClick = (latlng) => {
    if (mode !== "routes") return;
    const point = { name: "Point on map", lat: latlng.lat, lon: latlng.lng };
    setEnds((prev) => (!prev.from || prev.to ? { from: point, to: null } : { ...prev, to: point }));
  };

  const selectStreet = (id) => {
    setSelectedId(id);
    setHighlight([]);
  };

  if (error && !areas.length) {
    return (
      <div className="fatal">
        <h1>Shade Routes</h1>
        <p>{error}</p>
        <p>On your laptop, start the backend first: open a terminal in the <code>backend</code> folder and run{" "}
          <code>uvicorn main:app --reload</code>.</p>
      </div>
    );
  }

  return (
    <div className={`app mode-${mode}${selectedId && mode === "planner" ? " with-detail" : ""}`}>
      <TopBar
        areas={areas} areaKey={areaKey} onArea={changeArea}
        mode={mode} onMode={setMode} hour={hour} onHour={setHour}
        onCompare={() => setShowCompare(true)}
      />
      <div className="body">
        <aside className="side">
          {mode === "planner" ? (
            <PlannerPanel area={area} ranking={ranking} hour={hour} selectedId={selectedId} onSelect={selectStreet} />
          ) : (
            <RoutesPanel
              area={area} areaKey={areaKey} ends={ends} onEnds={setEnds}
              hour={hour} onHour={setHour} route={route}
            />
          )}
        </aside>
        <main className="map-wrap">
          <MapView
            area={area} streets={streets} mode={mode} hour={hour}
            selectedId={selectedId} highlight={highlight} route={route} ends={ends} focus={focus}
            onSelectStreet={selectStreet} onMapClick={handleMapClick}
          />
        </main>
        {mode === "planner" && selectedId && (
          <StreetPanel
            areaKey={areaKey} streetId={selectedId} hour={hour}
            onClose={() => setSelectedId(null)} onFocus={setFocus}
          />
        )}
      </div>
      {areaKey && <ChatPanel areaKey={areaKey} areaName={area?.name} onActions={handleChatActions} />}
      {showCompare && <CompareModal onClose={() => setShowCompare(false)} />}
    </div>
  );
}
