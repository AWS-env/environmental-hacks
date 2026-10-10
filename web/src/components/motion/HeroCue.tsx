/**
 * "scroll to explore" prompt framed by four corner crosses, as on lusion.co's hero.
 * SmoothPage animates it through the `data-*` hooks and wires the click.
 */
export default function HeroCue() {
  const corners = ["left-0 top-0", "right-0 top-0", "bottom-0 left-0", "bottom-0 right-0"];
  return (
    <button
      type="button"
      data-scroll-cue
      className="absolute bottom-10 left-1/2 -translate-x-1/2 px-10 py-6 text-xs uppercase tracking-[0.3em] text-ink-2 transition-colors hover:text-ink"
    >
      {corners.map((pos) => (
        <span key={pos} data-cross aria-hidden="true" className={`absolute h-3 w-3 ${pos}`}>
          <span className="absolute left-0 top-1/2 h-px w-full bg-current" />
          <span className="absolute left-1/2 top-0 h-full w-px bg-current" />
        </span>
      ))}
      <span className="flex flex-col items-center gap-4">
        scroll to explore
        <span aria-hidden="true" className="relative block h-8 w-px overflow-hidden bg-line">
          <span data-cue-tick className="absolute inset-x-0 top-0 block h-3 bg-accent-strong" />
        </span>
      </span>
    </button>
  );
}
