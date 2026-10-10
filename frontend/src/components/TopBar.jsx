import { hourLabel } from "../theme.js";

const HOURS = [8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18];

export default function TopBar({ areas, areaKey, onArea, mode, onMode, hour, onHour, onCompare }) {
  const date = areas[0]?.date ? new Date(`${areas[0].date}T00:00:00`) : null;
  const day = date ? date.toLocaleDateString("en-IN", { day: "numeric", month: "short" }) : "";

  return (
    <header className="topbar">
      <div className="brand">
        <svg width="26" height="26" viewBox="0 0 26 26" aria-hidden="true">
          <rect width="26" height="26" rx="7" fill="#1d2b26" />
          <circle cx="13" cy="11" r="5.5" fill="#7fbcae" />
          <rect x="12" y="14" width="2" height="7" rx="1" fill="#e8e5dc" />
        </svg>
        <span>Shade Routes</span>
      </div>

      <div className="controls">
        <div className="tabs" role="tablist" aria-label="Mode">
          <button role="tab" aria-selected={mode === "planner"} className={mode === "planner" ? "on" : ""}
                  onClick={() => onMode("planner")}>Planner</button>
          <button role="tab" aria-selected={mode === "routes"} className={mode === "routes" ? "on" : ""}
                  onClick={() => onMode("routes")}>Routes</button>
        </div>

        <label className="select">
          <span className="sr-only">Area</span>
          <select value={areaKey ?? ""} onChange={(e) => onArea(e.target.value)}>
            {areas.map((a) => <option key={a.key} value={a.key}>{a.name}</option>)}
          </select>
        </label>

        <label className="select time">
          <span className="sr-only">Time of day</span>
          <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true">
            <circle cx="8" cy="8" r="6.5" fill="none" stroke="currentColor" strokeWidth="1.4" />
            <path d="M8 4.5V8l2.5 1.5" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
          </svg>
          <select value={hour} onChange={(e) => onHour(Number(e.target.value))}>
            {HOURS.map((h) => <option key={h} value={h}>{hourLabel(h)}{day ? `, ${day}` : ""}</option>)}
          </select>
        </label>

        <button className="ghost" onClick={onCompare}>Compare areas</button>
      </div>

      <div className="role">
        <span>{mode === "planner" ? "City planner" : "Walker"}</span>
        <span className="avatar" aria-hidden="true">{mode === "planner" ? "CP" : "W"}</span>
      </div>
    </header>
  );
}
