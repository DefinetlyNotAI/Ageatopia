"use client";
import { useMemo, useRef, useState } from "react";
import { duration } from "@/lib/types";

const WIDTH = 560;
const HEIGHT = 220;
const LEFT = 58;
const RIGHT = 16;
const TOP = 18;
const BOTTOM = 34;
const PLOT_WIDTH = WIDTH - LEFT - RIGHT;
const PLOT_HEIGHT = HEIGHT - TOP - BOTTOM;

function numberLabel(value: number) {
  return value.toLocaleString(undefined, { maximumFractionDigits: 2 });
}

export function Trend({ title, points, unit = "", color = "var(--accent)", live = false }: {
  title: string; points: [number, number][]; unit?: string; color?: string; live?: boolean;
}) {
  const samples = useMemo(() => points.filter(([x, y]) => Number.isFinite(x) && Number.isFinite(y)), [points]);
  const [hasCustomView, setHasCustomView] = useState(false);
  const [viewSize, setViewSize] = useState(0);
  const [offsetFromLatest, setOffsetFromLatest] = useState(0);
  const [selectedIndex, setSelectedIndex] = useState<number | null>(null);
  const [hoveredIndex, setHoveredIndex] = useState<number | null>(null);
  const svgRef = useRef<SVGSVGElement>(null);
  const count = samples.length;
  const visibleCount = hasCustomView ? Math.min(count, Math.max(2, viewSize)) : count;
  const maxOffset = Math.max(0, count - visibleCount);
  const offset = Math.min(offsetFromLatest, maxOffset);
  const end = Math.max(0, count - 1 - offset);
  const start = Math.max(0, end - visibleCount + 1);
  const visible = samples.slice(start, end + 1);
  const minX = visible[0]?.[0] ?? 0;
  const maxX = visible.at(-1)?.[0] ?? 1;
  const minY = visible.reduce((minimum, point) => Math.min(minimum, point[1]), 0);
  const maxY = visible.reduce((maximum, point) => Math.max(maximum, point[1]), 1);
  const xRange = maxX - minX || 1;
  const yRange = maxY - minY || 1;
  const xAt = (point: [number, number], index: number) => maxX === minX
    ? LEFT + (visible.length <= 1 ? PLOT_WIDTH : index / (visible.length - 1) * PLOT_WIDTH)
    : LEFT + (point[0] - minX) / xRange * PLOT_WIDTH;
  const yAt = (value: number) => TOP + (maxY - value) / yRange * PLOT_HEIGHT;
  const coordinates = visible.map((point, index) => `${xAt(point, index)},${yAt(point[1])}`).join(" ");
  const currentIndex = selectedIndex !== null && selectedIndex >= start && selectedIndex <= end ? selectedIndex : end;
  const current = samples[currentIndex];
  const currentLocalIndex = currentIndex - start;
  const hover = hoveredIndex !== null && hoveredIndex >= start && hoveredIndex <= end ? samples[hoveredIndex] : null;
  const hoverY = hover ? yAt(hover[1]) : 0;

  function resetView() {
    setHasCustomView(false);
    setViewSize(0);
    setOffsetFromLatest(0);
    setSelectedIndex(null);
    setHoveredIndex(null);
  }

  function zoomIn() {
    if (visibleCount <= 2) return;
    const nextSize = Math.max(2, Math.ceil(visibleCount / 2));
    setHasCustomView(true);
    setViewSize(nextSize);
    setOffsetFromLatest(Math.min(offset, Math.max(0, count - nextSize)));
  }

  function zoomOut() {
    if (visibleCount >= count) return;
    const nextSize = Math.min(count, Math.ceil(visibleCount * 1.6));
    if (nextSize >= count) {
      resetView();
      return;
    }
    setHasCustomView(true);
    setViewSize(nextSize);
  }

  function moveSelection(nextIndex: number) {
    setSelectedIndex(Math.max(start, Math.min(end, nextIndex)));
    setHoveredIndex(null);
  }

  function showSample(index: number) {
    if (index < start || index > end) {
      const nextStart = Math.max(0, Math.min(index - Math.floor(visibleCount / 2), count - visibleCount));
      const nextEnd = nextStart + visibleCount - 1;
      setOffsetFromLatest(Math.max(0, count - 1 - nextEnd));
    }
    setSelectedIndex(index);
    setHoveredIndex(null);
  }

  function sampleAtPointer(clientX: number) {
    const rect = svgRef.current?.getBoundingClientRect();
    if (!rect || !visible.length) return null;
    const chartX = (clientX - rect.left) / rect.width * WIDTH;
    const fraction = Math.max(0, Math.min(1, (chartX - LEFT) / PLOT_WIDTH));
    const target = minX + fraction * xRange;
    let nearest = 0;
    let distance = Number.POSITIVE_INFINITY;
    visible.forEach((point, index) => {
      const nextDistance = Math.abs(point[0] - target);
      if (nextDistance < distance) { nearest = index; distance = nextDistance; }
    });
    return start + nearest;
  }

  if (!count) return <section className="chart"><h3>{title}</h3><p className="muted">No recorded samples</p></section>;

  const sampleId = `sample-${title.replace(/[^a-z0-9]+/gi, "-").toLowerCase()}`;
  const earlierStep = Math.max(1, Math.floor(visibleCount / 2));
  return <section className="chart" aria-label={title}>
    <div className="chart-heading"><h3>{title}</h3><span className="muted">{live ? "Live updates" : "Recorded samples"}</span></div>
    <div className="chart-controls" aria-label={`${title} chart controls`}>
      <button type="button" aria-label={`Zoom in on ${title}`} onClick={zoomIn} disabled={visibleCount <= 2}>Zoom in</button>
      <button type="button" aria-label={`Zoom out on ${title}`} onClick={zoomOut} disabled={visibleCount >= count}>Zoom out</button>
      <button type="button" aria-label={`Pan ${title} earlier`} onClick={() => setOffsetFromLatest(Math.min(maxOffset, offset + earlierStep))} disabled={offset >= maxOffset}>Earlier</button>
      <button type="button" aria-label={`Pan ${title} later`} onClick={() => setOffsetFromLatest(Math.max(0, offset - earlierStep))} disabled={offset <= 0}>Later</button>
      <button type="button" onClick={() => { setOffsetFromLatest(0); setHoveredIndex(null); }} disabled={offset === 0}>Follow latest</button>
      <button type="button" onClick={resetView} disabled={!hasCustomView && offset === 0}>Reset view</button>
      <span className="chart-window">Samples {start + 1}–{end + 1} of {count}</span>
    </div>
    <div className="chart-plot">
      <svg ref={svgRef} viewBox={`0 0 ${WIDTH} ${HEIGHT}`} role="img" tabIndex={0}
        aria-label={`${title}. ${count} samples. Latest: ${numberLabel(samples.at(-1)![1])} ${unit}. Use left and right arrow keys to inspect visible samples.`}
        onPointerMove={event => {
          const index = sampleAtPointer(event.clientX);
          if (index !== null) { setHoveredIndex(index); setSelectedIndex(index); }
        }}
        onPointerLeave={() => setHoveredIndex(null)}
        onKeyDown={event => {
          if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
            event.preventDefault();
            moveSelection(currentIndex + (event.key === "ArrowLeft" ? -1 : 1));
          } else if (event.key === "Home") {
            event.preventDefault(); moveSelection(start);
          } else if (event.key === "End") {
            event.preventDefault(); moveSelection(end);
          }
        }}>
        {[0, 1, 2, 3].map(tick => {
          const y = TOP + tick * PLOT_HEIGHT / 3;
          const value = maxY - tick * yRange / 3;
          return <g key={tick}>
            <line x1={LEFT} x2={WIDTH - RIGHT} y1={y} y2={y} stroke="var(--line)" strokeDasharray={tick ? "2 4" : undefined} />
            <text x={LEFT - 8} y={y + 3} textAnchor="end">{numberLabel(value)}</text>
          </g>;
        })}
        <line x1={LEFT} x2={WIDTH - RIGHT} y1={HEIGHT - BOTTOM} y2={HEIGHT - BOTTOM} stroke="var(--line)" />
        <polyline points={coordinates} fill="none" stroke={color} strokeWidth="2.2" vectorEffect="non-scaling-stroke" />
        {current && <circle cx={xAt(current, currentLocalIndex)} cy={yAt(current[1])} r="4" fill="var(--paper)" stroke={color} strokeWidth="2" vectorEffect="non-scaling-stroke" />}
        <text x={LEFT} y={HEIGHT - 9}>{duration(minX)}</text>
        <text x={WIDTH - RIGHT} y={HEIGHT - 9} textAnchor="end">{duration(maxX)}</text>
      </svg>
      {hover && <div className="chart-tooltip" style={{ left: `${Math.min(88, Math.max(12, xAt(hover, hoveredIndex! - start) / WIDTH * 100))}%`, top: `${Math.max(12, hoverY / HEIGHT * 100)}%` }} role="status">
        <strong>{numberLabel(hover[1])} {unit}</strong><span>{duration(hover[0])}</span>
      </div>}
    </div>
    <div className="chart-sample-control">
      <label htmlFor={sampleId}>Selected sample · {current ? `${duration(current[0])} · ${numberLabel(current[1])} ${unit}` : "none"}</label>
      <input id={sampleId} type="range" min={start} max={end} value={currentIndex} disabled={visibleCount < 2}
        aria-label={`Select a sample from ${title}`} onChange={event => moveSelection(Number(event.target.value))} />
    </div>
    <details className="chart-samples">
      <summary>Sample table · {count} records</summary>
      <div className="sample-table-scroll"><table><thead><tr><th>Simulation time</th><th>Value{unit ? ` (${unit})` : ""}</th><th><span className="sr-only">Chart selection</span></th></tr></thead>
        <tbody>{samples.map(([simMs, value], index) => <tr key={`${simMs}-${index}`}>
          <td className="mono">{duration(simMs)}</td><td>{numberLabel(value)} {unit}</td>
          <td><button type="button" className="text-link" onClick={() => showSample(index)}>Show on chart</button></td>
        </tr>)}</tbody></table></div>
    </details>
  </section>;
}

export function Bars({ title, values }: { title: string; values: [string, number][] }) {
  const max = Math.max(1, ...values.map(v => v[1]));
  return <section className="chart"><h3>{title}</h3>{!values.length ? <p className="muted">No recorded data</p> :
    values.map(([label, value]) => <div key={label} className="bar-row"><span title={label}>{label}</span>
      <div className="bar-track"><div style={{ width: `${value/max*100}%` }} /></div><strong>{value}</strong></div>)}
  </section>;
}
