import { useEffect, useMemo, useRef } from "react";
import L from "leaflet";
import { CircleMarker, GeoJSON, MapContainer, Polyline, TileLayer, Tooltip, useMap, useMapEvents } from "react-leaflet";
import { FASTEST, LEVEL_NAMES, NO_DEMAND, PRIORITY, SHADIEST, STREET_GREY, level, levelColor, pct } from "../theme.js";

// Canvas drawing keeps thousands of streets fast; tolerance makes thin lines easy to click
const renderer = L.canvas({ tolerance: 6 });
const toLatLng = (coords) => coords.map(([lon, lat]) => [lat, lon]);

function Recenter({ area, focus }) {
  const map = useMap();
  useEffect(() => {
    if (!area) return;
    const [lat, lon] = area.center;
    const dLat = area.radius / 111320;
    const dLon = area.radius / (111320 * Math.cos((lat * Math.PI) / 180));
    map.fitBounds([[lat - dLat, lon - dLon], [lat + dLat, lon + dLon]], { padding: [16, 16] });
  }, [area?.key]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (focus) map.fitBounds(focus, { padding: [60, 60], maxZoom: 17 });
  }, [focus, map]);
  return null;
}

function ClickCatcher({ onClick }) {
  useMapEvents({ click: (e) => onClick(e.latlng) });
  return null;
}

function Legend({ mode }) {
  if (mode === "routes") {
    return (
      <div className="legend">
        <div className="legend-title">Routes</div>
        <div className="legend-row"><svg width="28" height="8"><line x1="0" y1="4" x2="28" y2="4" stroke={FASTEST} strokeWidth="4" strokeDasharray="6 5" /></svg>Fastest</div>
        <div className="legend-row"><svg width="28" height="8"><line x1="0" y1="4" x2="28" y2="4" stroke={SHADIEST} strokeWidth="5" /></svg>Shadiest</div>
      </div>
    );
  }
  return (
    <div className="legend">
      <div className="legend-title">Planting priority</div>
      <div className="ramp">
        {PRIORITY.map((c, i) => <span key={c} style={{ background: c }} title={LEVEL_NAMES[i]} />)}
      </div>
      <div className="ramp-labels"><span>Lower</span><span>Plant first</span></div>
      <div className="legend-row small"><span className="swatch" style={{ background: NO_DEMAND }} />No simulated walkers</div>
    </div>
  );
}

function Insight({ area }) {
  const hourly = area?.stats?.hourly_shade;
  if (!hourly) return null;
  const noon = hourly[area.hours.indexOf(12)];
  return (
    <div className="insight">
      <strong>Midday: only {pct(noon)} of street area is shaded</strong>
      <span>At noon buildings cast almost no shadow. Trees are what cool the street.</span>
    </div>
  );
}

export default function MapView({ area, streets, mode, selectedId, highlight, route, ends, focus,
                                  onSelectStreet, onMapClick }) {
  const latest = useRef({ mode, onSelectStreet });
  latest.current = { mode, onSelectStreet };

  const style = (feature) => {
    if (mode === "routes") return { color: STREET_GREY, weight: 2, opacity: 0.7 };
    const s = feature.properties.score;
    return { color: levelColor(s), weight: 1.5 + level(s) * 0.8, opacity: 0.95 };
  };

  const onEachFeature = (feature, layer) => {
    layer.bindTooltip(feature.properties.name, { sticky: true, direction: "top", offset: [0, -6] });
    layer.on("click", (e) => {
      if (latest.current.mode !== "planner") return;    // in route mode the click picks a point
      L.DomEvent.stopPropagation(e);
      latest.current.onSelectStreet(feature.properties.street);
    });
  };

  const picked = useMemo(() => {
    if (!streets) return null;
    const ids = new Set([selectedId, ...highlight].filter(Boolean));
    if (!ids.size) return null;
    return { type: "FeatureCollection", features: streets.features.filter((f) => ids.has(f.properties.street)) };
  }, [streets, selectedId, highlight]);

  if (!area) return <div className="map-empty">Loading areas…</div>;

  return (
    <>
      <MapContainer center={area.center} zoom={15} className="map" zoomControl={false} preferCanvas>
        <TileLayer
          attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors &copy; <a href="https://carto.com/attributions">CARTO</a>'
          url="https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png"
          subdomains="abcd" maxZoom={19}
        />
        <Recenter area={area} focus={focus} />
        <ClickCatcher onClick={onMapClick} />
        {streets && <GeoJSON key={`${area.key}-${mode}`} data={streets} style={style} renderer={renderer}
                              onEachFeature={onEachFeature} />}
        {picked && mode === "planner" && (
          <GeoJSON key={`picked-${selectedId}-${highlight.join(",")}`} data={picked} interactive={false}
                   renderer={renderer} style={() => ({ color: "#1d1d1b", weight: 9, opacity: 0.35 })} />
        )}
        {route && !route.error && (
          <>
            <Polyline positions={toLatLng(route.shadiest.coords)} pathOptions={{ color: SHADIEST, weight: 7, opacity: 0.9 }} />
            <Polyline positions={toLatLng(route.fastest.coords)} pathOptions={{ color: FASTEST, weight: 4, dashArray: "8 8" }} />
          </>
        )}
        {mode === "routes" && ["from", "to"].map((k) => ends[k] && (
          <CircleMarker key={k} center={[ends[k].lat, ends[k].lon]} radius={8}
                        pathOptions={{ color: "#1d1d1b", weight: 3, fillColor: "#fff", fillOpacity: 1 }}>
            <Tooltip permanent direction="right" offset={[10, 0]} className="end-label">
              {k === "from" ? "Start" : "End"}: {ends[k].name}
            </Tooltip>
          </CircleMarker>
        ))}
      </MapContainer>
      <Legend mode={mode} />
      {mode === "planner" && <Insight area={area} />}
      {mode === "routes" && !ends.from && <div className="map-hint">Click the map to set a start point</div>}
      {mode === "routes" && ends.from && !ends.to && <div className="map-hint">Now click the end point</div>}
    </>
  );
}
