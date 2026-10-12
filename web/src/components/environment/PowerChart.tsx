"use client";

import { useEffect, useMemo, useRef, useState, type KeyboardEvent, type PointerEvent } from "react";
import { fmtDuration, fmtWatts, type TimelinePoint } from "@/lib/run-it";

const HEIGHT = 230;
const PAD = { l: 46, r: 14, t: 14, b: 28 };
const SCOPE_COLOR = "#e9eef0";
const SYSTEM_COLOR = "#8a979d";

/** Axis ticks on 1/2/5 steps from zero up to just above the largest value. */
function niceTicks(value: number): number[] {
  const rough = Math.max(value, 0.1) / 4;
  const exp = 10 ** Math.floor(Math.log10(rough));
  const step = ([1, 2, 5, 10].map((m) => m * exp).find((v) => v >= rough) ?? 10 * exp);
  const count = Math.max(1, Math.ceil(value / step));
  return Array.from({ length: count + 1 }, (_, i) => i * step);
}

const clock = (t: number) => `${Math.floor(t / 60)}:${String(Math.floor(t % 60)).padStart(2, "0")}`;

/** Path through the points, broken where a value is missing (never drawn as zero). */
function linePath(values: (number | null)[], x: (i: number) => number, y: (v: number) => number): string {
  let d = "", pen = false;
  values.forEach((v, i) => {
    if (v === null) { pen = false; return; }
    d += `${pen ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`;
    pen = true;
  });
  return d;
}

/**
 * Measured power over the session: the measured scope (the command, or your processes) and the whole Mac.
 * One axis (W). Hover or arrow keys read every series at that moment; the table view holds every sample.
 */
export default function PowerChart({ points, scopeLabel, scope }: { points: TimelinePoint[]; scopeLabel: string; scope: "app" | "user" }) {
  const box = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(0);
  const [hover, setHover] = useState<number | null>(null);

  useEffect(() => {
    const node = box.current;
    if (!node) return;
    const observer = new ResizeObserver(([entry]) => setWidth(entry.contentRect.width));
    observer.observe(node);
    return () => observer.disconnect();
  }, []);

  const scopeValues = useMemo(() => points.map((p) => (scope === "app" ? p.app : p.user)), [points, scope]);
  const systemValues = useMemo(() => points.map((p) => p.system), [points]);
  const hasSystem = systemValues.some((v) => v !== null);
  const yTicks = niceTicks(Math.max(0, ...scopeValues.map((v) => v ?? 0), ...systemValues.map((v) => v ?? 0)) * 1.05);
  const max = yTicks[yTicks.length - 1];
  const step = yTicks[1] ?? max;
  const inner = Math.max(1, width - PAD.l - PAD.r);
  const t0 = points[0]?.t ?? 0, t1 = points[points.length - 1]?.t ?? 1;
  const x = (i: number) => PAD.l + (t1 > t0 ? ((points[i].t - t0) / (t1 - t0)) * inner : inner / 2);
  const y = (v: number) => PAD.t + (1 - v / max) * (HEIGHT - PAD.t - PAD.b);
  const xTicks = points.length > 1 ? Array.from({ length: 5 }, (_, k) => t0 + ((t1 - t0) * k) / 4) : [];
  const scopePath = linePath(scopeValues, x, y);
  const lastScope = scopeValues.filter((v) => v !== null).at(-1) ?? null;
  const lastSystem = systemValues.filter((v) => v !== null).at(-1) ?? null;
  const area = scopePath && scopeValues.every((v) => v !== null) && points.length > 1
    ? `${scopePath}L${x(points.length - 1).toFixed(1)},${y(0)}L${x(0).toFixed(1)},${y(0)}Z` : "";

  const pick = (clientX: number) => {
    const rect = box.current?.getBoundingClientRect();
    if (!rect || points.length === 0) return;
    const px = clientX - rect.left;
    let best = 0;
    points.forEach((_, i) => { if (Math.abs(x(i) - px) < Math.abs(x(best) - px)) best = i; });
    setHover(best);
  };
  const onKey = (event: KeyboardEvent<SVGSVGElement>) => {
    if (!points.length) return;
    const step = event.key === "ArrowRight" ? 1 : event.key === "ArrowLeft" ? -1 : 0;
    if (!step) return;
    event.preventDefault();
    setHover((h) => Math.min(points.length - 1, Math.max(0, (h ?? points.length - 1) + step)));
  };
  const h = hover !== null && hover < points.length ? hover : null;

  return (
    <div className="flex flex-col gap-3">
      <div className="rp-legend" aria-hidden="true">
        <span className="max-w-full"><svg width="18" height="6" className="shrink-0"><line x1="0" y1="3" x2="18" y2="3" stroke={SCOPE_COLOR} strokeWidth="2" /></svg><span className="truncate">{scopeLabel}</span> <b>{fmtWatts(lastScope)}</b></span>
        {hasSystem && <span><svg width="18" height="6"><line x1="0" y1="3" x2="18" y2="3" stroke={SYSTEM_COLOR} strokeWidth="2" strokeDasharray="4 3" /></svg>Whole Mac (coarse) <b>{fmtWatts(lastSystem)}</b></span>}
      </div>
      <div ref={box} className="relative" style={{ height: HEIGHT }}>
        {width > 0 && points.length > 1 ? (
          <svg
            width={width} height={HEIGHT} role="img" tabIndex={0}
            aria-label={`Power over ${fmtDuration(t1 - t0)}: ${scopeLabel} peaked at ${fmtWatts(Math.max(0, ...scopeValues.map((v) => v ?? 0)))}. Use the left and right arrow keys to read samples.`}
            onPointerMove={(e: PointerEvent<SVGSVGElement>) => pick(e.clientX)} onPointerLeave={() => setHover(null)}
            onKeyDown={onKey} onBlur={() => setHover(null)} className="block outline-none"
          >
            <defs>
              <linearGradient id="power-fill" x1="0" x2="0" y1="0" y2="1">
                <stop offset="0" stopColor={SCOPE_COLOR} stopOpacity="0.16" />
                <stop offset="1" stopColor={SCOPE_COLOR} stopOpacity="0" />
              </linearGradient>
            </defs>
            {yTicks.map((v) => (
              <g key={v}>
                <line x1={PAD.l} x2={width - PAD.r} y1={y(v)} y2={y(v)} stroke="#ffffff" strokeOpacity={v === 0 ? 0.14 : 0.05} />
                <text x={PAD.l - 8} y={y(v)} dy="0.32em" textAnchor="end" fontSize="10" fill="#7f8c92">{v.toFixed(step < 1 ? 1 : 0)} W</text>
              </g>
            ))}
            {xTicks.map((t, k) => (
              <text key={k} x={PAD.l + (t1 > t0 ? ((t - t0) / (t1 - t0)) * inner : 0)} y={HEIGHT - 8}
                textAnchor={k === 0 ? "start" : k === 4 ? "end" : "middle"} fontSize="10" fill="#7f8c92">{clock(t)}</text>
            ))}
            {area && <path d={area} fill="url(#power-fill)" />}
            {hasSystem && <path d={linePath(systemValues, x, y)} fill="none" stroke={SYSTEM_COLOR} strokeWidth="1.5" strokeDasharray="5 4" strokeLinejoin="round" />}
            <path d={scopePath} fill="none" stroke={SCOPE_COLOR} strokeWidth="2" strokeLinejoin="round" strokeLinecap="round" />
            {h !== null && (
              <g pointerEvents="none">
                <line x1={x(h)} x2={x(h)} y1={PAD.t} y2={HEIGHT - PAD.b} stroke="#ffffff" strokeOpacity="0.35" />
                {scopeValues[h] !== null && <circle cx={x(h)} cy={y(scopeValues[h]!)} r="4.5" fill={SCOPE_COLOR} stroke="#0a0d0e" strokeWidth="2" />}
                {systemValues[h] !== null && <circle cx={x(h)} cy={y(systemValues[h]!)} r="4" fill={SYSTEM_COLOR} stroke="#0a0d0e" strokeWidth="2" />}
              </g>
            )}
          </svg>
        ) : (
          <p className="rp-empty">Collecting samples… the chart starts after the second reading.</p>
        )}
        {h !== null && width > 0 && (
          <div role="status" className="pointer-events-none absolute top-1 rounded-lg border border-[#ffffff26] bg-[#111b20f0] px-3 py-2 text-[11px] shadow-xl"
            style={{ left: Math.min(Math.max(x(h) + 12, PAD.l), width - 190) }}>
            <div className="mb-1 text-[#9aabb2]">{clock(points[h].t)} into the session</div>
            <div className="flex justify-between gap-5"><span className="text-[#c9d2d6]">{scopeLabel}</span><b className="font-medium text-white">{fmtWatts(scopeValues[h])}</b></div>
            {hasSystem && <div className="flex justify-between gap-5"><span className="text-[#c9d2d6]">Whole Mac</span><b className="font-medium text-white">{fmtWatts(systemValues[h])}</b></div>}
          </div>
        )}
      </div>
      {points.length > 1 && (
        <details className="text-[12px]">
          <summary className="cursor-pointer select-none text-[#a9b5bb] hover:text-white">Show the samples as a table</summary>
          <div className="rp-table-wrap mt-2 max-h-64 overflow-y-auto">
            <table className="rp-table">
              <thead><tr><th>Time</th><th className="text-right">{scopeLabel}</th>{hasSystem && <th className="text-right">Whole Mac</th>}</tr></thead>
              <tbody>
                {points.map((p, i) => (
                  <tr key={i}><td className="font-mono">{clock(p.t)}</td><td className="text-right font-mono">{fmtWatts(scopeValues[i])}</td>{hasSystem && <td className="text-right font-mono">{fmtWatts(p.system)}</td>}</tr>
                ))}
              </tbody>
            </table>
          </div>
        </details>
      )}
    </div>
  );
}
