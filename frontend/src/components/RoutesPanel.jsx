import { useEffect, useState } from "react";
import { api } from "../api.js";
import { FASTEST, SHADIEST, hourLabel } from "../theme.js";

function PlaceInput({ label, value, areaKey, onPick }) {
  const [query, setQuery] = useState(value?.name ?? "");
  const [options, setOptions] = useState([]);
  const [open, setOpen] = useState(false);

  useEffect(() => setQuery(value?.name ?? ""), [value]);

  useEffect(() => {
    if (!open || query.trim().length < 2) {
      setOptions([]);
      return;
    }
    const t = setTimeout(() => api.places(areaKey, query).then(setOptions).catch(() => setOptions([])), 200);
    return () => clearTimeout(t);
  }, [query, open, areaKey]);

  return (
    <label className="place">
      <span className="field-label">{label}</span>
      <input
        value={query} placeholder="Metro gate, market, bus stop…"
        onChange={(e) => { setQuery(e.target.value); setOpen(true); }}
        onFocus={() => setOpen(true)} onBlur={() => setTimeout(() => setOpen(false), 150)}
      />
      {open && options.length > 0 && (
        <ul className="options">
          {options.map((p) => (
            <li key={p.name}>
              <button type="button" onMouseDown={() => { onPick(p); setOpen(false); }}>
                {p.name} <small>{p.kind}</small>
              </button>
            </li>
          ))}
        </ul>
      )}
    </label>
  );
}

function RouteCard({ kind, data, recommended }) {
  const color = kind === "fastest" ? FASTEST : SHADIEST;
  return (
    <div className={`route-card ${kind}`}>
      <div className="route-head">
        <svg width="26" height="8" aria-hidden="true">
          <line x1="0" y1="4" x2="26" y2="4" stroke={color} strokeWidth={kind === "fastest" ? 4 : 5}
                strokeDasharray={kind === "fastest" ? "6 5" : undefined} />
        </svg>
        <strong>{kind === "fastest" ? "Fastest" : "Shadiest"}</strong>
        {recommended && <span className="tag">Recommended</span>}
      </div>
      <div className="route-nums">
        <span><strong>{data.length_m.toLocaleString("en-IN")} m</strong></span>
        <span>{data.minutes} min</span>
        <span>{data.shade_pct}% in shade</span>
      </div>
      <div className="meter" aria-hidden="true"><span style={{ width: `${data.shade_pct}%`, background: color }} /></div>
    </div>
  );
}

export default function RoutesPanel({ area, areaKey, ends, onEnds, hour, onHour, route }) {
  return (
    <div className="panel">
      <h2>Find a cooler route</h2>
      <p className="sub">{area?.name} · New Delhi</p>

      <PlaceInput label="From" value={ends.from} areaKey={areaKey} onPick={(p) => onEnds({ ...ends, from: p })} />
      <PlaceInput label="To" value={ends.to} areaKey={areaKey} onPick={(p) => onEnds({ ...ends, to: p })} />
      <p className="hint">Or click two points on the map.</p>

      <label className="slider">
        <span className="field-label">Walking at <strong>{hourLabel(hour)}</strong></span>
        <input type="range" min="8" max="18" step="1" value={hour} onChange={(e) => onHour(Number(e.target.value))} />
        <span className="slider-ends"><span>8 AM</span><span>1 PM</span><span>6 PM</span></span>
      </label>

      {route?.error && <p className="error">{route.error}</p>}
      {route && !route.error && (
        <>
          <RouteCard kind="fastest" data={route.fastest} />
          <RouteCard kind="shadiest" data={route.shadiest} recommended={route.shadiest.shade_pct > route.fastest.shade_pct} />
          <div className="callout">{route.summary}</div>
        </>
      )}
      {(ends.from || ends.to) && (
        <button className="ghost wide" onClick={() => onEnds({ from: null, to: null })}>Clear route</button>
      )}
      <p className="note">Simulated pilot estimates for {area?.date ? new Date(`${area.date}T00:00:00`).toLocaleDateString("en-IN", { day: "numeric", month: "long" }) : "a hot day"}.</p>
    </div>
  );
}
