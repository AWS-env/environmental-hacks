"use client";

import { createContext, useContext, useEffect, useMemo, useRef } from "react";
import type { ReactNode } from "react";
import { usePathname, useRouter } from "next/navigation";
import { skipIntroReplay } from "@/components/intro/config";
import { ParticleField } from "./ParticleField";
import { EXIT_DURATION, collectBlocks, enterBlocks, exitBlocks, exitDelay } from "./pageBlocks";
import "./route-transition.css";

/** Particles keep floating at least this long after the page has gone, even when the next one is instant. */
const MIN_FLOAT_MS = 250;
/** Give up waiting for the next route after this long, and bring the old page back. */
const ARRIVAL_TIMEOUT_MS = 15000;

interface RouteTransitionApi {
  /**
   * For `<Link onNavigate>`: takes over the navigation to `href` and plays the transition.
   * Leaves it alone (plain navigation) for the current route or with reduced motion.
   */
  intercept(event: { preventDefault(): void }, href: string): void;
}

const RouteTransitionContext = createContext<RouteTransitionApi | null>(null);

export const useRouteTransition = () => useContext(RouteTransitionContext);

const sleep = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms));
const nextFrames = (count: number) =>
  new Promise<void>((resolve) => {
    const tick = () => (--count <= 0 ? resolve() : requestAnimationFrame(tick));
    requestAnimationFrame(tick);
  });

/**
 * Page-to-page transition, mounted once in the root layout so it outlives every route:
 * the current page slides out left shedding dust, the dust floats until the next route has
 * rendered underneath, types that route's path, then scatters as the new page slides in.
 * Elements marked `data-route-keep` (the sidebar) stay put above it all.
 */
export default function RouteTransition({ children }: { children: ReactNode }) {
  const router = useRouter();
  const pathname = usePathname();
  const rootRef = useRef<HTMLDivElement>(null);
  const veilRef = useRef<HTMLDivElement>(null);
  const backdropRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const busy = useRef(false);
  const onArrive = useRef<(() => void) | null>(null);
  const pathnameRef = useRef(pathname);

  // Any route commit ends the wait (including back/forward pressed mid-transition).
  useEffect(() => {
    pathnameRef.current = pathname;
    onArrive.current?.();
  }, [pathname]);

  const api = useMemo<RouteTransitionApi>(() => {
    async function play(href: string) {
      const root = rootRef.current, veil = veilRef.current, backdrop = backdropRef.current, canvas = canvasRef.current;
      if (!root || !veil || !backdrop || !canvas) return router.push(href);
      const html = document.documentElement;
      const css = getComputedStyle(html);
      let field: ParticleField;
      try {
        field = new ParticleField(canvas, {
          base: css.getPropertyValue("--ink").trim() || "#f3f4f6",
          glow: html.dataset.theme === "dark" ? "#f7c062" : css.getPropertyValue("--accent-strong").trim() || "#0d7a5a",
        });
      } catch {
        return router.push(href);
      }

      busy.current = true;
      html.dataset.routeTransition = "";
      veil.hidden = false;
      const veilAnimations: Animation[] = [];
      let exits: Animation[] = [];
      try {
        // 1. Exit: blocks slide left and shed dust while the backdrop closes over the page.
        const blocks = collectBlocks(root);
        const rects = blocks.map((el) => el.getBoundingClientRect());
        exits = exitBlocks(blocks);
        field.start(rects.map((rect, i) => ({ rect, delay: exitDelay(i, blocks.length) })));
        const exitMs = (exitDelay(blocks.length - 1, blocks.length) + EXIT_DURATION) * 1000;
        veilAnimations.push(
          backdrop.animate([{ opacity: 0 }, { opacity: 1 }], {
            duration: Math.max(exitMs - 150, 300),
            delay: 150,
            easing: "cubic-bezier(0.4, 0, 1, 1)",
            fill: "forwards",
          }),
        );
        await sleep(exitMs + 60);

        // 2. Float: navigate under the opaque veil and wait for the new route to commit.
        const from = pathnameRef.current;
        const arrived = new Promise<boolean>((resolve) => {
          onArrive.current = () => {
            if (pathnameRef.current !== from) resolve(true);
          };
          setTimeout(() => resolve(false), ARRIVAL_TIMEOUT_MS);
        });
        if (href === "/") skipIntroReplay();
        router.push(href);
        const [ok] = await Promise.all([arrived, sleep(MIN_FLOAT_MS)]);
        onArrive.current = null;
        await nextFrames(2);
        // Anything that survived the route change (none today) must not stay hidden.
        exits.forEach((a) => a.cancel());

        // 3. Type the route, then 4. scatter as the new page slides in.
        let enters: Animation[] = [];
        if (ok) {
          enters = enterBlocks(collectBlocks(root));
          await field.type(pathnameRef.current, css.getPropertyValue("--font-geist-mono").trim() || "monospace");
        }
        const scattered = field.scatter();
        const opening = backdrop.animate([{ opacity: 1 }, { opacity: 0 }], {
          duration: 600,
          delay: 80,
          easing: "cubic-bezier(0.33, 0, 0.2, 1)",
          fill: "forwards",
        });
        veilAnimations.push(opening);
        enters.forEach((a) => a.play());
        await Promise.all([scattered, opening.finished.catch(() => undefined)]);
      } finally {
        field.destroy();
        exits.forEach((a) => a.cancel());
        veil.hidden = true;
        veilAnimations.forEach((a) => a.cancel());
        delete html.dataset.routeTransition;
        onArrive.current = null;
        busy.current = false;
      }
    }

    return {
      intercept(event, href) {
        if (busy.current) return event.preventDefault();
        if (href === pathnameRef.current || window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
        event.preventDefault();
        void play(href);
      },
    };
  }, [router]);

  return (
    <RouteTransitionContext.Provider value={api}>
      {/* display: contents keeps the body's flex layout exactly as if this wrapper were not here. */}
      <div ref={rootRef} style={{ display: "contents" }}>
        {children}
      </div>
      <div ref={veilRef} className="route-veil" hidden aria-hidden="true">
        <div ref={backdropRef} className="route-veil__backdrop" />
        <canvas ref={canvasRef} className="route-veil__canvas" />
      </div>
    </RouteTransitionContext.Provider>
  );
}
