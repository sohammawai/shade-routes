import { fmt, hourLabel, level, levelColor, pct } from "../theme.js";

export function ScoreBadge({ score }) {
  const dark = level(score) >= 3;
  return (
    <span className="score" style={{ background: levelColor(score), color: dark ? "#fff" : "#1d1d1b" }}>
      {score}<small>/100</small>
    </span>
  );
}

export default function PlannerPanel({ area, ranking, hour, selectedId, onSelect }) {
  if (!area) return <div className="panel"><p className="hint">Loading…</p></div>;
  const s = area.stats;

  return (
    <div className="panel">
      <h2>Where to plant first</h2>
      <p className="sub">{area.name} · New Delhi</p>

      <div className="hero-card">
        <span className="hero">{pct(s.busy_low_shade_share)}</span>
        <span>of the busiest walking streets get under 20% shade at 2 PM</span>
      </div>
      <div className="stat-row">
        <div className="stat"><strong>{fmt(s.segments)}</strong><span>street segments analysed</span></div>
        <div className="stat"><strong>{fmt(s.simulated_trips)}</strong><span>simulated daily trips</span></div>
      </div>

      <h3>Ranked planting priority</h3>
      <p className="hint">Simulated pilot estimates · shade at {hourLabel(hour)}</p>
      <ol className="rank-list">
        {ranking.map((r) => (
          <li key={r.id}>
            <button className={r.id === selectedId ? "on" : ""} onClick={() => onSelect(r.id)}>
              <span className="rank">{String(r.rank).padStart(2, "0")}</span>
              <span className="main">
                <span className="name">{r.name}</span>
                <span className="meta">{fmt(r.trips_per_day)} trips/day · {r.shade_pct}% shade at {hourLabel(r.hour)}</span>
              </span>
              <ScoreBadge score={r.score} />
            </button>
          </li>
        ))}
      </ol>
      <p className="note">
        Results show where to look first. Footpath width and underground utilities are not included.
      </p>
    </div>
  );
}
