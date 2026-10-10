"use client";

import { useEffect, useId, useLayoutEffect, useRef, useState, type SVGProps } from "react";
import { gsap } from "gsap";

function SmoothPath({ d, ...props }: SVGProps<SVGPathElement> & { d: string }) {
  const element = useRef<SVGPathElement>(null);
  const previous = useRef(d);
  useLayoutEffect(() => {
    const node = element.current;
    if (!node) return;
    const start = previous.current;
    previous.current = d;
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches || start === d) return;
    const tween = gsap.fromTo(node, { attr: { d: start } }, { attr: { d }, duration: 1.5, ease: "power2.inOut" });
    return () => { previous.current = node.getAttribute("d") || d; tween.kill(); };
  }, [d]);
  return <path ref={element} d={d} {...props} />;
}

type Series = { label: string; values: number[]; muted?: boolean };
// Integer hashing and rounded samples render identically on server and browser.
const fraction = (value: number) => {
  let n = Math.imul(Math.round(value * 10000) ^ 0x9e3779b9, 0x85ebca6b);
  n ^= n >>> 13;
  return (n >>> 0) / 4294967296;
};

export function sampleResource(t: number, device = 0) {
  const cpu = Math.max(8, Math.min(96, 43 + device * 8 + Math.sin(t * .13 + device) * 17 + Math.sin(t * .47) * 8 + (fraction(t + device * 19) - .5) * 12));
  const memory = Math.max(24, Math.min(91, 65 + device * 4 + Math.sin(t * .067 + 1) * 9 + Math.sin(t * .21) * 3));
  return { cpu: Math.round(cpu*1000)/1000, memory: Math.round(memory*1000)/1000, temperature: Math.round(42 + cpu * .4), processes: Math.round(166 + device * 23 + cpu * .35) };
}

export function ResourceChart({ series, frame, minutes = 15, max = 100, suffix = "%", small = false }: {
  series: Series[]; frame: number; minutes?: number; max?: number; suffix?: string; small?: boolean;
}) {
  const id = useId().replaceAll(":", "");
  const [hover, setHover] = useState<number | null>(null);
  const container = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState({ width: 740, height: small ? 74 : 178 });
  useEffect(() => {
    if (!container.current) return;
    const observer = new ResizeObserver(([entry]) => {
      const width = Math.round(entry.contentRect.width), height = Math.round(entry.contentRect.height);
      if (width > 0 && height > 0) setSize(previous => previous.width === width && previous.height === height ? previous : { width, height });
    });
    observer.observe(container.current);
    return () => observer.disconnect();
  }, []);
  const { width, height } = size;
  const left = small ? 0 : 42, right = width - (small ? 0 : 8), top = 8, bottom = height - (small ? 2 : 27);
  const count = series[0]?.values.length || 1;
  const x = (index: number) => left + index / Math.max(count - 1, 1) * (right - left);
  const y = (value: number) => bottom - Math.max(0, Math.min(max, value)) / max * (bottom - top);
  const clock = (index: number) => {
    const total = 13 * 60 + 40 + frame * .25 - minutes + index / Math.max(count - 1, 1) * minutes;
    const bounded = ((Math.floor(total) % 1440) + 1440) % 1440;
    return `${String(Math.floor(bounded / 60)).padStart(2, "0")}:${String(bounded % 60).padStart(2, "0")}`;
  };
  return (
    <div ref={container} className={`resource-chart ${small ? "is-small" : ""}`}>
      <svg viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none" role="img" aria-label={`${series.map(item => item.label).join(" and ")} generated history`} onPointerLeave={() => setHover(null)} onPointerMove={small ? undefined : event => {
        const rect = event.currentTarget.getBoundingClientRect();
        setHover(Math.max(0, Math.min(count - 1, Math.round(((event.clientX - rect.left) / rect.width * width - left) / (right - left) * (count - 1)))));
      }}>
        <defs>
          <clipPath id={`${id}-clip`}><rect x={left} y={top} width={right-left} height={bottom-top} /></clipPath>
          <linearGradient id={`${id}-fill`} x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor="white" stopOpacity=".18" /><stop offset="100%" stopColor="white" stopOpacity="0" /></linearGradient>
        </defs>
        {!small && [0, .25, .5, .75, 1].map(tick => <g key={tick}>
          <line x1={left} x2={right} y1={y(tick*max)} y2={y(tick*max)} stroke="white" strokeOpacity=".09" strokeDasharray="2 4" />
          <text x={left-9} y={y(tick*max)+4} textAnchor="end" fill="#8d999f" fontSize="11">{tick*max}{suffix}</text>
        </g>)}
        {!small && Array.from({length:7}, (_, i) => <g key={i}>
          <line x1={x(i*(count-1)/6)} x2={x(i*(count-1)/6)} y1={top} y2={bottom} stroke="white" strokeOpacity=".055" />
          <text x={x(i*(count-1)/6)} y={height-4} textAnchor={i===0 ? "start" : i===6 ? "end" : "middle"} fill="#8d999f" fontSize="10">{clock(i*(count-1)/6)}</text>
        </g>)}
        <g clipPath={`url(#${id}-clip)`}>
          {series.map((item, s) => {
            const points = item.values.map((value, i) => `${x(i)},${y(value)}`).join(" L ");
            if (!points) return null;
            return <g key={item.label} opacity={item.muted ? .48 : 1}>
              <SmoothPath d={`M ${left},${bottom} L ${points} L ${right},${bottom} Z`} fill={`url(#${id}-fill)`} />
              {Array.from({length:small ? 90 : 360}, (_, i) => {
                const position = fraction(i + s*991) * (count-1);
                const start = Math.floor(position), blend = position-start;
                const value = item.values[start]*(1-blend) + item.values[Math.min(start+1,count-1)]*blend;
                const ceiling = y(value);
                return <circle key={i} cx={x(position)} cy={ceiling + (bottom-ceiling)*fraction(i*3.7+9)} r={fraction(i+7)*.7+.25} fill="white" opacity={.12+fraction(i+11)*.48} />;
              })}
              <SmoothPath d={`M ${points}`} fill="none" stroke="#e9eff1" strokeWidth={small ? 1.2 : 1.5} vectorEffect="non-scaling-stroke" strokeLinejoin="round" strokeLinecap="round" />
            </g>;
          })}
          {hover !== null && !small && <g>
            <line x1={x(hover)} x2={x(hover)} y1={top} y2={bottom} stroke="white" strokeOpacity=".35" strokeDasharray="3 3" />
            {series.map(item => <circle key={item.label} cx={x(hover)} cy={y(item.values[hover])} r="3.5" fill="white" />)}
          </g>}
        </g>
      </svg>
      {hover !== null && !small && <div className="chart-tooltip"><span>{clock(hover)}</span>{series.map(item => <strong key={item.label}>{item.label} <b>{item.values[hover].toFixed(1)}{suffix}</b></strong>)}</div>}
    </div>
  );
}
