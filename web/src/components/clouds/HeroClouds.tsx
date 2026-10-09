"use client";

import { useRef } from "react";
import type { CSSProperties } from "react";
import { INTRO_END_EVENT, START_DEADLINE_MS, getIntroDecision } from "@/components/intro/config";
import { TOTAL_DURATION } from "@/components/intro/timeline";
import { MOTION_OK, gsap, useGSAP } from "@/components/motion/gsap";
import { CLOUD_PIECES } from "./pieces.generated";

/** Enter anyway if the intro never reports its end (debug hold, failed chunk load). */
const INTRO_END_FALLBACK_MS = START_DEADLINE_MS + TOTAL_DURATION * 1000 + 1500;

/**
 * Pixel-art cloud banks hanging off both edges of the first screen
 * (art: assets-src/clouds/generate-clouds.mjs, one set per theme). The art is a
 * CSS background chosen by theme, so only the visible set is downloaded.
 *
 * Here: they slide in from their edge once the intro overlay clears, then drift
 * and swell slowly forever. SmoothPage parts them outward as the page scrolls
 * (`data-cloud-part`), since its triggers must be created after the smoother.
 *
 * Reduced motion shows them still.
 */
export default function HeroClouds() {
  const rootRef = useRef<HTMLDivElement>(null);

  useGSAP(
    () => {
      const mm = gsap.matchMedia();
      mm.add(MOTION_OK, () => {
        const clouds = gsap.utils.toArray<HTMLElement>("[data-cloud]", rootRef.current);
        const outward = (el: HTMLElement) => (el.dataset.cloud === "left" ? -1 : 1);

        // Ambient motion: each bank sways and bobs on its own slow, unrelated clock. Both only move
        // away from the screen, so a bank never comes loose from the edges it is cut by.
        clouds.forEach((el) => {
          const dir = outward(el);
          gsap.to(el, {
            x: dir * gsap.utils.random(12, 26),
            y: (el.dataset.anchor === "top" ? -1 : 1) * gsap.utils.random(6, 12),
            duration: gsap.utils.random(7, 11),
            delay: gsap.utils.random(0, 2),
            ease: "sine.inOut",
            yoyo: true,
            repeat: -1,
          });
          gsap.to(el, {
            scale: 1.025,
            duration: gsap.utils.random(5, 8),
            ease: "sine.inOut",
            yoyo: true,
            repeat: -1,
          });
        });

        // Entrance: hidden past their edge until the page is visible, then they roll in.
        gsap.set(clouds, { autoAlpha: 0, xPercent: (_, el: HTMLElement) => outward(el) * 35 });
        const enter = () =>
          gsap.to(clouds, { autoAlpha: 1, xPercent: 0, duration: 2.8, ease: "power3.out", stagger: 0.15, overwrite: "auto" });

        if (getIntroDecision().mode !== "intro") {
          enter();
          return;
        }
        let entered = false;
        const onEnd = () => {
          if (entered) return;
          entered = true;
          enter();
        };
        const fallback = window.setTimeout(onEnd, INTRO_END_FALLBACK_MS);
        window.addEventListener(INTRO_END_EVENT, onEnd);
        return () => {
          window.clearTimeout(fallback);
          window.removeEventListener(INTRO_END_EVENT, onEnd);
        };
      });
      return () => mm.revert();
    },
    { scope: rootRef },
  );

  return (
    <div
      ref={rootRef}
      aria-hidden="true"
      // Banks are sized by the hero's height; small screens shrink them toward their edge.
      className="pointer-events-none absolute inset-0 overflow-x-clip [--cloud-scale:0.42] sm:[--cloud-scale:0.7] lg:[--cloud-scale:1]"
    >
      {CLOUD_PIECES.map((piece) => (
        <div
          key={piece.name}
          data-cloud-part={piece.side === "left" ? -1 : 1}
          className="absolute"
          style={{
            [piece.side]: 0,
            [piece.anchor]: `calc(${piece.offset}% * var(--cloud-scale))`,
            height: `calc(${piece.height}% * var(--cloud-scale))`,
            aspectRatio: `${piece.width} / ${piece.pixelHeight}`,
          }}
        >
          <div
            data-cloud={piece.side}
            data-anchor={piece.anchor}
            className="h-full w-full bg-(image:--cloud-light) bg-no-repeat will-change-transform dark:bg-(image:--cloud-dark)"
            style={
              {
                "--cloud-light": `url(/clouds/light/${piece.name}.svg)`,
                "--cloud-dark": `url(/clouds/dark/${piece.name}.svg)`,
                backgroundSize: "100% 100%",
                transformOrigin: `${piece.side} ${piece.anchor}`,
              } as CSSProperties
            }
          />
        </div>
      ))}
    </div>
  );
}
