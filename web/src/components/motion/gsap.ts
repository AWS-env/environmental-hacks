"use client";

import gsap from "gsap";
import { ScrollTrigger } from "gsap/ScrollTrigger";
import { ScrollSmoother } from "gsap/ScrollSmoother";
import { SplitText } from "gsap/SplitText";
import { useGSAP } from "@gsap/react";

// Registered once, here, so every motion module shares the same plugin set.
gsap.registerPlugin(useGSAP, ScrollTrigger, ScrollSmoother, SplitText);

export { gsap, ScrollTrigger, ScrollSmoother, SplitText, useGSAP };

export const MOTION_OK = "(prefers-reduced-motion: no-preference)";
export const MOTION_REDUCED = "(prefers-reduced-motion: reduce)";

/** Initial pose for anything revealed on scroll. The pose is set from JS, so without JS everything stays visible. */
export const REVEAL_FROM = { autoAlpha: 0, yPercent: 12 } as const;

/** Reveals elements as they enter the viewport, staggering those that arrive together. */
export function revealOnScroll(targets: gsap.DOMTarget, start = "top 90%") {
  if (!targets) return;
  if (Array.isArray(targets) && targets.length === 0) return;
  if (typeof NodeList !== "undefined" && targets instanceof NodeList && targets.length === 0) return;
  gsap.set(targets, REVEAL_FROM);
  ScrollTrigger.batch(targets, {
    start,
    once: true,
    onEnter: (batch) =>
      gsap.to(batch, {
        autoAlpha: 1,
        yPercent: 0,
        duration: 1,
        ease: "power3.out",
        stagger: 0.1,
        overwrite: true,
      }),
  });
}
