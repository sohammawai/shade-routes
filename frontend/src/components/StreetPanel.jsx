import { useEffect, useState } from "react";
import { api } from "../api.js";
import { fmt, hourLabel } from "../theme.js";
import { ScoreBadge } from "./PlannerPanel.jsx";
import ShadeBars from "./ShadeBars.jsx";

export default function StreetPanel({ areaKey, streetId, hour, onClose, onFocus }) {
  const [detail, setDetail] = useState(null);
  const [trees, setTrees] = useState(24);
  const [sim, setSim] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    let live = true;
    setDetail(null);
    setSim(null);
    setError("");
    api.street(areaKey, streetId)
      .then((d) => {
        if (!live) return;
        setDetail(d);
        setTrees(Math.min(24, d.max_trees));
        onFocus(d.bounds);
      })
      .catch((e) => live && setError(e.message));
    return () => {
      live = false;
    };
  }, [areaKey, streetId]); // eslint-disable-line react-hooks/exhaustive-deps

  const simulate = async () => {
    setBusy(true);
    try {
      setSim(await api.simulate(areaKey, streetId, trees, hour));
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  const hi = detail ? detail.hours.indexOf(hour) : -1;
  const before = detail && hi >= 0 ? Math.round(detail.shade[hi] * 100) : null;
  const after = sim && hi >= 0 ? Math.round(sim.after[hi] * 100) : null;

  return (
    <aside className="detail" aria-label="Street detail">
      <button className="close" onClick={onClose} aria-label="Close">×</button>
      {error && <p className="error">{error}</p>}
      {!detail && !error && <p className="hint">Loading street…</p>}
      {detail && (
        <>
          <p className="eyebrow">Selected street</p>
          <div className="detail-head">
            <h2>{detail.name}</h2>
            <ScoreBadge score={detail.score} />
          </div>
          <p className="hint">Simulated pilot estimates{detail.rank ? ` · ranked #${detail.rank}` : ""}</p>

          <div className="stat-row">
            <div className="stat"><strong>{detail.length_m >= 1000 ? `${(detail.length_m / 1000).toFixed(1)} km` : `${detail.length_m} m`}</strong><span>street length</span></div>
            <div className="stat"><strong>{fmt(detail.trips_per_day)}</strong><span>daily walking trips</span></div>
          </div>

          <h3>Shade by hour</h3>
          <ShadeBars hours={detail.hours} before={detail.shade} after={sim?.after} hour={hour} />

          <h3>Plant trees</h3>
          <div className="plant">
            <div className="stepper" role="group" aria-label="Number of trees">
              <button onClick={() => setTrees((t) => Math.max(1, t - 4))} aria-label="Fewer trees">−</button>
              <input type="number" min="1" max={detail.max_trees} value={trees}
                     onChange={(e) => setTrees(Math.max(1, Math.min(detail.max_trees, Number(e.target.value) || 1)))} />
              <button onClick={() => setTrees((t) => Math.min(detail.max_trees, t + 4))} aria-label="More trees">+</button>
            </div>
            <button className="primary" onClick={simulate} disabled={busy}>{busy ? "Simulating…" : "Simulate"}</button>
          </div>
          <p className="hint">Room for up to {fmt(detail.max_trees)} trees along this street.</p>

          {sim && (
            <div className="result">
              <div className="before-after">
                <div className="tile sun"><span>Today at {hourLabel(hour)}</span><strong>{before}%</strong></div>
                <span className="arrow" aria-hidden="true">→</span>
                <div className="tile shade"><span>With {sim.trees} new trees</span><strong>{after}%</strong></div>
              </div>
              <p className="benefit">
                <strong>{fmt(sim.walkers_per_day)}</strong> simulated daily walkers use this street; about{" "}
                <strong>{fmt(sim.shaded_km_per_day)} km</strong> of their daily walking moves into shade.
              </p>
              <p className="note">{sim.assumptions}</p>
            </div>
          )}
        </>
      )}
    </aside>
  );
}
