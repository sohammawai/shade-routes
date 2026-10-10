import { SHADE_LIGHT, SHADIEST, hourLabel, shortHour } from "../theme.js";

// Shade by hour for one street: today's shade, plus the shade new trees would add.
export default function ShadeBars({ hours, before, after, hour }) {
  const W = 320, H = 150, left = 38, right = 8, top = 10, bottom = 26;
  const plotW = W - left - right, plotH = H - top - bottom;
  const band = plotW / hours.length;
  const barW = Math.min(18, band - 6);
  const y = (v) => top + plotH * (1 - v);

  return (
    <figure className="shade-bars">
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Shade by hour">
        {[0, 0.5, 1].map((t) => (
          <g key={t}>
            <line x1={left} x2={W - right} y1={y(t)} y2={y(t)} stroke="#e1e0d9" strokeWidth="1" />
            <text x={left - 6} y={y(t) + 4} textAnchor="end" className="tick">{t * 100}%</text>
          </g>
        ))}
        {hours.map((h, i) => {
          const x = left + i * band + (band - barW) / 2;
          const b = before[i];
          const a = after ? after[i] : b;
          const current = h === hour;
          return (
            <g key={h}>
              <rect x={x} y={y(b)} width={barW} height={Math.max(0, y(0) - y(b))} rx="2"
                    fill={after ? SHADE_LIGHT : current ? SHADIEST : SHADE_LIGHT}>
                <title>{`${hourLabel(h)}: ${Math.round(b * 100)}% shade today`}</title>
              </rect>
              {after && a - b > 0.005 && (
                <rect x={x} y={y(a)} width={barW} height={Math.max(0, y(b) - y(a) - 2)} rx="2" fill={SHADIEST}>
                  <title>{`${hourLabel(h)}: ${Math.round(a * 100)}% with new trees`}</title>
                </rect>
              )}
              {(i % 2 === 0 || current) && (
                <text x={x + barW / 2} y={H - 8} textAnchor="middle" className={current ? "tick on" : "tick"}>
                  {shortHour(h)}
                </text>
              )}
            </g>
          );
        })}
      </svg>
      {after && (
        <figcaption className="bars-key">
          <span><i style={{ background: SHADE_LIGHT }} />Shade today</span>
          <span><i style={{ background: SHADIEST }} />Added by new trees</span>
        </figcaption>
      )}
    </figure>
  );
}
