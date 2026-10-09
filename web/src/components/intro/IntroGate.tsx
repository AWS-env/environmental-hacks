"use client";

import dynamic from "next/dynamic";
import { useCallback, useState, useSyncExternalStore } from "react";
import { BOOT_SCRIPT, getIntroDecision } from "./config";

// three.js and the model only load when the intro actually plays.
const IntroCanvas = dynamic(() => import("./IntroCanvas"), { ssr: false });

// The server snapshot is null, so hydration matches the SSR markup.
const getServerDecision = () => null;
const subscribe = () => () => {};

type Status = "loading" | "playing" | "done";

/**
 * Landing intro and hero tree. The page renders normally underneath; this only
 * adds layers, so content, SEO and LCP do not wait on the effect.
 *
 * The intro plays on every load of the landing page, then the tree stays behind
 * the first screen and fades out as the page scrolls. Without the intro
 * (`?intro=0`, reduced motion, a slow start) the tree just fades in. Only
 * devices without WebGL2, or whose canvas fails, get no tree.
 *
 * The black cover is server-rendered, so the page never flashes before the
 * intro starts. An inline script hides it before first paint for `?intro=0`,
 * CSS hides it for reduced motion and as a failsafe, and <noscript> hides it
 * without JavaScript.
 */
export default function IntroGate() {
  const current = useSyncExternalStore(subscribe, getIntroDecision, getServerDecision);
  const [status, setStatus] = useState<Status>("loading");

  const handleReady = useCallback(() => setStatus("playing"), []);
  const handleDone = useCallback(() => setStatus("done"), []);

  const playing = current?.mode === "intro" && status !== "done";
  const tree = current && current.mode !== "none" && status !== "done" ? current : null;
  return (
    <>
      <div
        id="intro-cover"
        aria-hidden="true"
        // The boot script may tag this element before hydration.
        suppressHydrationWarning
        hidden={current !== null && (!playing || status === "playing")}
        data-state={playing ? "hold" : undefined}
      />
      <script dangerouslySetInnerHTML={{ __html: BOOT_SCRIPT }} />
      <noscript>
        <style>{"#intro-cover{display:none}"}</style>
      </noscript>
      {tree && <IntroCanvas mode={tree.mode} options={tree.options} onReady={handleReady} onDone={handleDone} />}
    </>
  );
}
