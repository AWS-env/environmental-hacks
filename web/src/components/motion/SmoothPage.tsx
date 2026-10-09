"use client";

import { useRef } from "react";
import type { ReactNode } from "react";
import { MOTION_OK, MOTION_REDUCED, ScrollSmoother, ScrollTrigger, SplitText, gsap, revealOnScroll, useGSAP } from "./gsap";

/** Fraction of the indicator track the thumb fills (lusion.co keeps a minimum of 20%). */
const INDICATOR_THUMB = 0.22;
/** Seconds the indicator stays visible after scrolling stops. */
const INDICATOR_HIDE_AFTER = 0.8;

/**
 * Fades the hero tree out over the first screen, in step with the cloud banks parting. The tree
 * mounts later than this effect, so it reads the progress from the `--tree-away` CSS variable.
 */
function fadeTreeWithScroll() {
  gsap.to(document.documentElement, {
    "--tree-away": 1,
    ease: "none",
    scrollTrigger: { start: 0, end: () => window.innerHeight, scrub: true, invalidateOnRefresh: true },
  });
}

/**
 * Smooth-scroll shell plus every scroll-driven effect on the landing page.
 *
 * Everything lives in one effect on purpose: ScrollSmoother has to exist before any
 * ScrollTrigger is created, and React runs child effects before parent effects, so
 * splitting this across components would build the triggers against native scroll.
 *
 * Markup contract (data attributes on children):
 * - `data-scroll-cue`   "scroll to explore" button; fades out over the first screen, click scrolls to `#audit`
 * - `data-split`        text split into lines that slide up out of a mask
 * - `data-reveal`       fades and rises in when it enters the viewport
 * - `data-hero-card`    grows from a small rounded panel into the full card as it scrolls in
 * - `data-cloud-part`   -1 or 1: slides off-screen that way (and swells) as the first screen scrolls away
 *                       (the hero tree fades and swells with it, via `--tree-away`)
 * - `data-count`        counts up to its value (`data-decimals`, `data-prefix`, `data-suffix`)
 * - `data-speed` / `data-lag`  ScrollSmoother parallax and trailing effects
 *
 * Reduced motion keeps native scrolling and shows everything immediately.
 */
export default function SmoothPage({ children }: { children: ReactNode }) {
  const rootRef = useRef<HTMLDivElement>(null);

  useGSAP(
    () => {
      const root = rootRef.current;
      if (!root) return;
      const q = gsap.utils.selector(root);
      const cue = q("[data-scroll-cue]")[0] as HTMLElement | undefined;
      const mm = gsap.matchMedia();

      mm.add(MOTION_OK, () => {
        const smoother = ScrollSmoother.create({
          wrapper: "#smooth-wrapper",
          content: "#smooth-content",
          smooth: 1.4,
          effects: true,
          ignoreMobileResize: true,
        });
        let onCue: (() => void) | undefined;

        // Headings and paragraphs: lines slide up out of a clipping mask, like lusion's titles.
        // autoSplit re-splits on resize and when web fonts finish loading.
        q("[data-split]").forEach((el) => {
          SplitText.create(el, {
            type: "lines",
            mask: "lines",
            autoSplit: true,
            onSplit: (self) =>
              gsap.from(self.lines, {
                yPercent: 115,
                duration: 1.2,
                ease: "power4.out",
                stagger: 0.09,
                scrollTrigger: { trigger: el, start: "top 88%", once: true },
              }),
          });
        });

        revealOnScroll(q("[data-reveal]"));

        // The hero panel starts as a small, heavily rounded card and opens up as it scrolls in.
        q("[data-hero-card]").forEach((card) => {
          gsap.fromTo(
            card,
            { scale: 0.84, borderRadius: 56 },
            {
              scale: 1,
              borderRadius: 16,
              ease: "none",
              scrollTrigger: { trigger: card, start: "top bottom", end: "top 30%", scrub: true },
            },
          );
        });

        // The hero's cloud banks part to either side, as if the camera were flying through them.
        q("[data-cloud-part]").forEach((el) => {
          const dir = Number(el.dataset.cloudPart);
          gsap.to(el, {
            xPercent: dir * 30,
            scale: 1.08,
            transformOrigin: dir < 0 ? "left center" : "right center",
            ease: "none",
            scrollTrigger: { start: 0, end: () => window.innerHeight, scrub: true, invalidateOnRefresh: true },
          });
        });

        fadeTreeWithScroll();

        q("[data-count]").forEach((el) => {
          const target = Number(el.dataset.count);
          const decimals = Number(el.dataset.decimals ?? 0);
          const prefix = el.dataset.prefix ?? "";
          const suffix = el.dataset.suffix ?? "";
          const state = { value: 0 };
          const write = () => {
            el.textContent = `${prefix}${state.value.toFixed(decimals)}${suffix}`;
          };
          write();
          gsap.to(state, {
            value: target,
            duration: 1.8,
            ease: "power2.out",
            onUpdate: write,
            scrollTrigger: { trigger: el, start: "top 92%", once: true },
          });
        });

        if (cue) {
          // The cue's corner crosses turn slowly and its tick slides down the line, forever.
          gsap.to(q("[data-cross]"), { rotation: 90, duration: 3, ease: "power2.inOut", repeat: -1, repeatDelay: 1, stagger: 0.15, transformOrigin: "50% 50%" });
          gsap.fromTo(q("[data-cue-tick]"), { yPercent: -120 }, { yPercent: 220, duration: 1.4, ease: "power2.inOut", repeat: -1, repeatDelay: 0.3 });
          gsap.to(cue, {
            autoAlpha: 0,
            y: -48,
            ease: "none",
            scrollTrigger: { trigger: cue, start: "top 85%", end: "top 40%", scrub: true },
          });
          onCue = () => smoother.scrollTo("#audit", true, "top 80px");
          cue.addEventListener("click", onCue);
        }

        // Thin progress bar on the right edge; shows while scrolling, hides shortly after.
        const indicator = q("[data-scroll-indicator]")[0] as HTMLElement | undefined;
        const thumb = q("[data-scroll-thumb]")[0] as HTMLElement | undefined;
        if (indicator && thumb) {
          const setY = gsap.quickSetter(thumb, "y", "px");
          const fadeOut = gsap.delayedCall(INDICATOR_HIDE_AFTER, () => {
            gsap.to(indicator, { opacity: 0, duration: 0.5, ease: "power2.out", overwrite: "auto" });
          });
          fadeOut.pause();
          ScrollTrigger.create({
            start: 0,
            end: "max",
            onUpdate: (self) => {
              setY(self.progress * (indicator.clientHeight - thumb.clientHeight));
              gsap.to(indicator, { opacity: 1, duration: 0.25, overwrite: "auto" });
              fadeOut.restart(true);
            },
          });
        }

        // Web fonts change line breaks and heights; trigger positions must follow.
        document.fonts?.ready.then(() => ScrollTrigger.refresh());

        // matchMedia only reverts gsap objects, so the DOM listener is removed here.
        return () => {
          if (cue && onCue) cue.removeEventListener("click", onCue);
          smoother.kill();
        };
      });

      // Reduced motion: no smoothing and no reveals; the cue still works, with the browser's own scrolling.
      mm.add(MOTION_REDUCED, () => {
        gsap.set(q("[data-scroll-indicator]"), { display: "none" });
        fadeTreeWithScroll();
        const onCue = () => document.getElementById("audit")?.scrollIntoView({ behavior: "auto" });
        cue?.addEventListener("click", onCue);
        return () => cue?.removeEventListener("click", onCue);
      });

      return () => mm.revert();
    },
    { scope: rootRef },
  );

  return (
    <div ref={rootRef}>
      <div
        data-scroll-indicator
        aria-hidden="true"
        className="pointer-events-none fixed right-2 top-[calc(50%-5rem)] z-40 h-40 w-[3px] rounded-full bg-line opacity-0"
      >
        <div
          data-scroll-thumb
          className="w-full rounded-full bg-accent-strong"
          style={{ height: `${INDICATOR_THUMB * 100}%` }}
        />
      </div>
      <div id="smooth-wrapper">
        <div id="smooth-content">{children}</div>
      </div>
    </div>
  );
}
