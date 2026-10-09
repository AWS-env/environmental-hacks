// Shared settings for the landing intro. Kept free of three.js imports so the
// gate can use it without pulling the WebGL chunk into the main bundle.

export const MODEL_URL = "/models/tree.glb";

/** If the page is not ready to play by this time after navigation, skip the intro. */
export const START_DEADLINE_MS = 3500;

export interface IntroOptions {
  /** `?intro=1` plays even past the load deadlines (dev-mode compiles often exceed them). */
  force: boolean;
  /** `?introDebug=1` shows the time scrubber. */
  debug: boolean;
  /** `?introT=3.2` freezes the timeline at that second, for frame comparisons. */
  freezeAt: number | null;
  /** Reduced motion: the tree is drawn once and never animates. */
  still: boolean;
}

/**
 * - `intro`: play the animation, then keep the tree on the page.
 * - `ambient`: skip the animation but still show the tree (`?intro=0`, reduced motion, slow start).
 * - `none`: no WebGL2, so there is no tree at all.
 */
export type IntroDecision =
  | { mode: "none" }
  | { mode: "ambient"; options: IntroOptions }
  | { mode: "intro"; options: IntroOptions };

function hasWebGL2(): boolean {
  try {
    return !!document.createElement("canvas").getContext("webgl2");
  } catch {
    return false;
  }
}

export function decideIntro(): IntroDecision {
  if (!hasWebGL2()) return { mode: "none" };
  const params = new URLSearchParams(window.location.search);
  const flag = params.get("intro");
  const force = flag === "1";
  const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const t = Number.parseFloat(params.get("introT") ?? "");
  const options: IntroOptions = {
    force,
    debug: params.get("introDebug") === "1",
    freezeAt: Number.isFinite(t) ? t : null,
    still: reducedMotion && !force,
  };
  const skipIntro = flag === "0" || (!force && (reducedMotion || performance.now() > START_DEADLINE_MS));
  return { mode: skipIntro ? "ambient" : "intro", options };
}

// Decided once per page load from client-only state (media queries, WebGL,
// load timing), so every caller sees the same answer.
let decision: IntroDecision | null = null;
export const getIntroDecision = () => (decision ??= decideIntro());

/**
 * Fired on `window` when the intro overlay stops covering the page: at the
 * handoff, after a skip, or when the canvas gives up.
 */
export const INTRO_END_EVENT = "intro:end";

// Runs inline, straight after the black cover is parsed and before first paint.
// It mirrors the cheap checks in decideIntro() so visitors who will not get the
// intro never see a black frame. The model download starts early for everyone,
// since the tree stays on the page even when the intro is skipped.
export const BOOT_SCRIPT = `(function(){try{
var c=document.getElementById("intro-cover");if(!c)return;
var l=document.createElement("link");l.rel="preload";l.as="fetch";l.crossOrigin="anonymous";l.href=${JSON.stringify(MODEL_URL)};
document.head.appendChild(l);
var f=new URLSearchParams(location.search).get("intro");
if(f==="0"||(f!=="1"&&matchMedia("(prefers-reduced-motion: reduce)").matches))c.setAttribute("data-boot","skip");
}catch(e){}})();`;
