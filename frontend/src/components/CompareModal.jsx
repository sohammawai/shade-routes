import { useEffect, useState } from "react";
import { api } from "../api.js";
import { AREA_COLORS, hourLabel, shortHour } from "../theme.js";

const W = 640, H = 300, L = 44, R = 16, T = 16, B = 30;

export default function CompareModal({ onClose }) {
  const [rows, setRows] = useState(null);
  const [hover, setHover] = useState(null);       // index of the hovered hour
  const [error, setError] = useState("");

  useEffect(() => {
    api.compare().then(setRows).catch((e) => setError(e.message));
    const onKey = (e) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const series = (rows ?? []).slice(0, AREA_COLORS.length).map((r, i) => ({ ...r, color: AREA_COLORS[i] }));
  const hours = series[0]?.hours ?? [];
  const x = (i) => L + (i * (W - L - R)) / Math.max(1, hours.length - 1);
  const y = (v) => T + (H - T - B) * (1 - v);
  const noon = hours.indexOf(12);
  const least = series.length && noon >= 0
    ? series.reduce((a, b) => (b.hourly_shade[noon] < a.hourly_shade[noon] ? b : a)) : null;

  const onMove = (e) => {
    const box = e.currentTarget.getBoundingClientRect();
    const px = ((e.clientX - box.left) / box.width) * W;
    const i = Math.round(((px - L) / (W - L - R)) * (hours.length - 1));
    setHover(Math.max(0, Math.min(hours.length - 1, i)));
  };

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal" role="dialog" aria-modal="true" aria-label="Compare areas" onClick={(e) => e.stopPropagation()}>
        <button className="close" onClick={onClose} aria-label="Close">×</button>
        {error && <p className="error">{error}</p>}
        {!rows && !error && <p className="hint">Loading…</p>}
        {series.length > 0 && (
          <>
            <h2>{least ? `At noon, ${least.name} has the least street shade (${Math.round(least.hourly_shade[noon] * 100)}%)` : "Street shade by area"}</h2>
            <p className="sub">Average shade on each area's streets, by hour of the day</p>
            <div className="chart-legend">
              {series.map((s) => <span key={s.key}><i style={{ background: s.color }} />{s.name}</span>)}
            </div>
            <div className="chart-wrap">
              <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Street shade by hour for each area"
                   onMouseMove={onMove} onMouseLeave={() => setHover(null)}>
                {[0, 0.25, 0.5, 0.75, 1].map((t) => (
                  <g key={t}>
                    <line x1={L} x2={W - R} y1={y(t)} y2={y(t)} stroke="#e1e0d9" strokeWidth="1" />
                    <text x={L - 8} y={y(t) + 4} textAnchor="end" className="tick">{t * 100}%</text>
                  </g>
                ))}
                {hours.map((h, i) => (
                  <text key={h} x={x(i)} y={H - 8} textAnchor="middle" className="tick">{shortHour(h)}</text>
                ))}
                {hover !== null && <line x1={x(hover)} x2={x(hover)} y1={T} y2={H - B} stroke="#c3c2b7" strokeWidth="1" />}
                {series.map((s) => (
                  <g key={s.key}>
                    <polyline fill="none" stroke={s.color} strokeWidth="2" strokeLinejoin="round" strokeLinecap="round"
                              points={s.hourly_shade.map((v, i) => `${x(i)},${y(v)}`).join(" ")} />
                    {s.hourly_shade.map((v, i) => (
                      <circle key={i} cx={x(i)} cy={y(v)} r={hover === i ? 5 : 4} fill={s.color} stroke="#fff" strokeWidth="2" />
                    ))}
                  </g>
                ))}
              </svg>
              {hover !== null && (
                <div className="chart-tip" style={{ left: `${(x(hover) / W) * 100}%` }}>
                  <strong>{hourLabel(hours[hover])}</strong>
                  {[...series].sort((a, b) => b.hourly_shade[hover] - a.hourly_shade[hover]).map((s) => (
                    <span key={s.key}><i style={{ background: s.color }} />{s.name}<b>{Math.round(s.hourly_shade[hover] * 100)}%</b></span>
                  ))}
                </div>
              )}
            </div>
            <table className="compare-table">
              <thead><tr><th>Area</th><th>8 AM</th><th>Noon</th><th>6 PM</th><th>Lowest</th></tr></thead>
              <tbody>
                {series.map((s) => {
                  const v = s.hourly_shade;
                  const lo = v.indexOf(Math.min(...v));
                  return (
                    <tr key={s.key}>
                      <td><i style={{ background: s.color }} />{s.name}</td>
                      <td>{Math.round(v[0] * 100)}%</td>
                      <td>{noon >= 0 ? `${Math.round(v[noon] * 100)}%` : "–"}</td>
                      <td>{Math.round(v[v.length - 1] * 100)}%</td>
                      <td>{Math.round(v[lo] * 100)}% at {hourLabel(hours[lo])}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </>
        )}
      </div>
    </div>
  );
}
