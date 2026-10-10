"use client";

import { useEffect, useEffectEvent, useRef, useState } from "react";
import { currentTheme } from "@/components/theme/boot";
import { INTRO_END_EVENT } from "./config";
import type { IntroOptions } from "./config";
import { IntroEngine } from "./IntroEngine";
import type { EndReason } from "./IntroEngine";
import { TOTAL_DURATION } from "./timeline";

// Loaded through next/dynamic, so three.js lives in this chunk only.

const LOAD_TIMEOUT_MS = 3000;
/** Fade to the page when the intro is skipped or fails. */
const LEAVE_MS = 300;
/** Fade the tree in after a skip, or when it appears without the intro. */
const ENTER_MS = 800;

/**
 * - `playing`: full-screen overlay running the intro; blocks the page.
 * - `settled`: the tree sits behind the page content and ignores the pointer.
 */
type Stage = "playing" | "settled";

interface Props {
  mode: "intro" | "ambient";
  options: IntroOptions;
  onReady(): void;
  /** The canvas gave up (error or slow device) and faded out; unmount it. */
  onDone(): void;
}

export default function IntroCanvas({ mode, options, onReady, onDone }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const engineRef = useRef<IntroEngine | null>(null);
  const ambient = mode === "ambient";
  const [stage, setStage] = useState<Stage>(ambient ? "settled" : "playing");
  // "skip" ends on the settled tree, "fail" removes the canvas.
  const [leaving, setLeaving] = useState<"skip" | "fail" | null>(null);
  // The intro covers the screen from the start; the ambient tree fades in once it has a frame.
  const [shown, setShown] = useState(!ambient);
  const debug = options.debug;

  const leave = useEffectEvent((kind: "skip" | "fail") => setLeaving((cur) => cur ?? kind));
  const ready = useEffectEvent(() => {
    setShown(true);
    onReady();
  });
  const ended = useEffectEvent((reason: EndReason) => {
    // "finished" just means the timeline is over; the tree stays.
    if (reason !== "finished") leave("fail");
  });

  useEffect(() => {
    const engine = new IntroEngine(
      containerRef.current!,
      {
        hold: debug || options.freezeAt !== null,
        freezeAt: options.freezeAt,
        ambient,
        still: options.still,
        loadTimeoutMs: options.force || ambient ? null : LOAD_TIMEOUT_MS,
      },
      {
        onReady: () => ready(),
        onPhase: (phase) => {
          if (phase === "handoff" && !debug) setStage("settled");
        },
        onEnd: (reason) => ended(reason),
      },
    );
    engineRef.current = engine;
    // The tree is lit and blended differently on the day and night pages; follow the toggle live.
    engine.setTheme(currentTheme());
    const themeObserver = new MutationObserver(() => engine.setTheme(currentTheme()));
    themeObserver.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
    return () => {
      themeObserver.disconnect();
      engineRef.current = null;
      engine.dispose();
    };
  }, [debug, ambient, options.force, options.freezeAt, options.still]);

  // Any key, click or scroll ends the intro early.
  useEffect(() => {
    if (debug || stage !== "playing") return;
    const skip = () => leave("skip");
    window.addEventListener("keydown", skip);
    window.addEventListener("pointerdown", skip);
    window.addEventListener("wheel", skip, { passive: true });
    return () => {
      window.removeEventListener("keydown", skip);
      window.removeEventListener("pointerdown", skip);
      window.removeEventListener("wheel", skip);
    };
  }, [debug, stage]);

  // The canvas keeps rendering under the CSS fade, then either jumps to the
  // settled tree (skip) or unmounts (failure).
  useEffect(() => {
    if (!leaving) return;
    const timer = setTimeout(() => {
      if (leaving === "fail") {
        window.dispatchEvent(new Event(INTRO_END_EVENT));
        onDone();
        return;
      }
      engineRef.current?.settle();
      setStage("settled");
      setLeaving(null);
    }, LEAVE_MS);
    return () => clearTimeout(timer);
  }, [leaving, onDone]);

  // Settled: the tree stays pinned behind the first screen. SmoothPage fades it out with the scroll
  // (`--tree-away`), and rendering pauses once it is fully gone.
  useEffect(() => {
    if (stage !== "settled") return;
    const sync = () => engineRef.current?.setSuspended(window.scrollY >= window.innerHeight);
    sync();
    window.addEventListener("scroll", sync, { passive: true });
    window.addEventListener("resize", sync);
    window.dispatchEvent(new Event(INTRO_END_EVENT));
    return () => {
      window.removeEventListener("scroll", sync);
      window.removeEventListener("resize", sync);
    };
  }, [stage]);

  const playing = stage === "playing";
  return (
    <>
      {/* Opacity is React's (skip/fail/enter fades); the inner container's is the
          scroll fade, driven by the `--tree-away` variable so scrolling never re-renders. */}
      <div
        aria-hidden="true"
        className="fixed inset-0 transition-opacity ease-out"
        style={{
          // Settled, it sits under the page content (which is positioned) and the header.
          zIndex: playing ? 100 : 0,
          opacity: leaving || !shown ? 0 : 1,
          transitionDuration: `${leaving ? LEAVE_MS : playing ? 0 : ENTER_MS}ms`,
          // A skip keeps the overlay hit-testable, so the click that skipped it
          // cannot land on the page. From the handoff on, the page is clickable.
          pointerEvents: playing ? "auto" : "none",
        }}
      >
        <div
          ref={containerRef}
          className="absolute inset-0 motion-safe:origin-center motion-safe:[transform:scale(calc(1_+_0.08*var(--tree-away,0)))]"
          style={{ opacity: "calc(1 - var(--tree-away, 0))" }}
        />
      </div>
      {debug && <DebugScrubber engineRef={engineRef} />}
    </>
  );
}

function DebugScrubber({ engineRef }: { engineRef: React.RefObject<IntroEngine | null> }) {
  const [time, setTime] = useState(0);
  const [playing, setPlaying] = useState(true);

  useEffect(() => {
    const id = setInterval(() => setTime(engineRef.current?.currentTime ?? 0), 100);
    return () => clearInterval(id);
  }, [engineRef]);

  return (
    <div className="fixed bottom-4 left-1/2 z-[101] flex w-[min(32rem,calc(100%-2rem))] -translate-x-1/2 items-center gap-3 rounded-lg border border-white/15 bg-black/80 px-3 py-2 font-mono text-xs text-gray-200">
      <button
        type="button"
        className="rounded border border-white/20 px-2 py-0.5 hover:bg-white/10"
        onClick={() => {
          const engine = engineRef.current;
          if (!engine) return;
          if (playing) engine.pause();
          else engine.play();
          setPlaying(!playing);
        }}
      >
        {playing ? "pause" : "play"}
      </button>
      <input
        type="range"
        min={0}
        max={TOTAL_DURATION}
        step={0.01}
        value={time}
        aria-label="Intro time"
        className="flex-1 accent-emerald-400"
        onChange={(e) => {
          const t = Number(e.target.value);
          engineRef.current?.seek(t);
          setTime(t);
          setPlaying(false);
        }}
      />
      <span className="w-12 text-right tabular-nums">{time.toFixed(2)}s</span>
    </div>
  );
}
