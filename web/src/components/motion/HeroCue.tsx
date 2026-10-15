"use client";

import Link from "next/link";
import { ArrowUpRight } from "lucide-react";
import { useRouteTransition } from "@/components/transition/RouteTransition";

/**
 * Hero "Continue" button framed by four corner crosses, as on lusion.co's hero. It links to /home
 * through the route transition. SmoothPage spins the crosses and fades the button out over the
 * first screen, using the `data-*` hooks.
 */
export default function HeroCue() {
  const transition = useRouteTransition();
  const corners = ["left-0 top-0", "right-0 top-0", "bottom-0 left-0", "bottom-0 right-0"];
  return (
    <div data-scroll-cue className="absolute bottom-10 left-1/2 z-30 -translate-x-1/2 px-6 py-5 text-ink-2">
      {corners.map((pos) => (
        <span key={pos} data-cross aria-hidden="true" className={`absolute h-3 w-3 ${pos}`}>
          <span className="absolute left-0 top-1/2 h-px w-full bg-current" />
          <span className="absolute left-1/2 top-0 h-full w-px bg-current" />
        </span>
      ))}
      <Link
        href="/home"
        onNavigate={(event) => transition?.intercept(event, "/home")}
        className="group inline-flex items-center gap-6 rounded-full border border-line bg-surface px-6 py-3 text-xs font-medium uppercase tracking-[0.18em] text-ink shadow-sm backdrop-blur-xl transition-colors hover:border-accent-line hover:bg-surface-2 focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-accent"
      >
        Continue
        <span aria-hidden="true" className="grid h-7 w-7 place-items-center rounded-full border border-line transition-colors group-hover:border-accent-line group-hover:text-accent-strong">
          <ArrowUpRight size={15} />
        </span>
      </Link>
    </div>
  );
}
