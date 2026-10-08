"use client";
import { duration } from "@/lib/types";

export function Trend({ title, points, unit = "", color = "var(--accent)" }: {
  title: string; points: [number, number][]; unit?: string; color?: string;
}) {
  if (!points.length) return <section className="chart"><h3>{title}</h3><p className="muted">No recorded samples</p></section>;
  const maxX = Math.max(1, ...points.map(p => p[0]));
  const maxY = Math.max(1, ...points.map(p => p[1]));
  const coordinates = points.map(([x, y]) => `${12 + (x/maxX)*476},${120 - (y/maxY)*100}`).join(" ");
  return <section className="chart"><h3>{title}</h3>
    <svg viewBox="0 0 500 150" role="img" aria-label={`${title}. Latest: ${points.at(-1)?.[1]} ${unit}`}>
      <path d="M12 20V120H488" fill="none" stroke="var(--line)" />
      <polyline points={coordinates} fill="none" stroke={color} strokeWidth="2" />
      <text x="12" y="143">0:00</text><text x="430" y="143">{duration(maxX)}</text>
      <text x="18" y="15">{maxY.toFixed(maxY % 1 ? 1 : 0)} {unit}</text>
    </svg>
  </section>;
}

export function Bars({ title, values }: { title: string; values: [string, number][] }) {
  const max = Math.max(1, ...values.map(v => v[1]));
  return <section className="chart"><h3>{title}</h3>{!values.length ? <p className="muted">No recorded data</p> :
    values.map(([label, value]) => <div key={label} className="bar-row"><span title={label}>{label}</span>
      <div className="bar-track"><div style={{ width: `${value/max*100}%` }} /></div><strong>{value}</strong></div>)}
  </section>;
}
