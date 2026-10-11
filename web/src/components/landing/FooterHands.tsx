"use client";

import { useRef } from "react";
import { MOTION_REDUCED, useGSAP } from "@/components/motion/gsap";
import { createFooterHands } from "./asciiHandsEngine";

/**
 * Footer closing scene: two ASCII hands reach in from either side with the copyright line
 * between them. The stage keeps the traced art's aspect ratio, so the hands span the full width.
 * Below `md` the art is cropped around the gap and the text sits underneath.
 */
export default function FooterHands() {
  const stageRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useGSAP(
    () => {
      const stage = stageRef.current;
      const canvas = canvasRef.current;
      if (!stage || !canvas) return;
      const hands = createFooterHands(stage, canvas, window.matchMedia(MOTION_REDUCED).matches);
      return () => hands.destroy();
    },
    { scope: stageRef },
  );

  return (
    <div className="relative mt-4 flex flex-col items-center md:block md:aspect-[1808/586] md:min-h-[340px]">
      <div
        ref={stageRef}
        aria-hidden="true"
        className="pointer-events-none relative h-[340px] w-full [mask-image:linear-gradient(to_bottom,black_86%,transparent)] md:absolute md:inset-0 md:h-full"
      >
        <canvas ref={canvasRef} className="absolute inset-0 h-full w-full" />
      </div>

      {/* Centred by an outer wrapper: `data-reveal` hands the inner element's transform to GSAP. */}
      <div className="pb-12 md:absolute md:inset-x-0 md:top-[36%] md:-translate-y-1/2 md:pb-0">
        <div data-reveal className="px-6 text-center text-sm leading-7 text-ink-3 md:p-0 md:text-lg md:leading-[1.67]">
          <p>© 2026</p>
          <p>EcoAudit.</p>
          <p>Read-only by design.</p>
        </div>
      </div>
    </div>
  );
}
