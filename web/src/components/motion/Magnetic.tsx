"use client";

import { useRef } from "react";
import type { ReactNode } from "react";
import { gsap, useGSAP } from "./gsap";

/**
 * Pulls its child toward the pointer and springs back on leave, like lusion's header buttons.
 * Only active for hover-capable pointers without a reduced-motion preference.
 */
export default function Magnetic({ children, strength = 0.35 }: { children: ReactNode; strength?: number }) {
  const ref = useRef<HTMLSpanElement>(null);

  useGSAP(() => {
    const el = ref.current;
    if (!el) return;
    const mm = gsap.matchMedia();
    mm.add("(hover: hover) and (prefers-reduced-motion: no-preference)", () => {
      const spring = { duration: 0.9, ease: "elastic.out(1, 0.35)" };
      const xTo = gsap.quickTo(el, "x", spring);
      const yTo = gsap.quickTo(el, "y", spring);
      const onMove = (e: PointerEvent) => {
        const r = el.getBoundingClientRect();
        xTo((e.clientX - (r.left + r.width / 2)) * strength);
        yTo((e.clientY - (r.top + r.height / 2)) * strength);
      };
      const onLeave = () => {
        xTo(0);
        yTo(0);
      };
      el.addEventListener("pointermove", onMove);
      el.addEventListener("pointerleave", onLeave);
      return () => {
        el.removeEventListener("pointermove", onMove);
        el.removeEventListener("pointerleave", onLeave);
      };
    });
    return () => mm.revert();
  }, [strength]);

  return (
    <span ref={ref} className="inline-block">
      {children}
    </span>
  );
}
