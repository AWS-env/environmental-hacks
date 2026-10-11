/**
 * Finds the visible "blocks" of a page and slides them out (left) or in (from the right).
 *
 * Uses the Web Animations API rather than GSAP on purpose: these animations layer over
 * whatever the page's own GSAP/ScrollSmoother code has set and never write inline styles,
 * so cancelling them leaves the page exactly as it was. They animate the individual
 * `translate` property, which composes with any `transform` the page already uses.
 */

/** Never animated: the sidebar (and anything else marked to stay), decorative layers, non-visual tags. */
const SKIP = "script,style,link,meta,noscript,template,[aria-hidden='true'],[data-route-keep],#intro-cover";
/** A visible box bigger than this share of the viewport is a container: animate its children instead. */
const CONTAINER_SHARE = 0.3;
const MAX_BLOCKS = 70, MAX_DEPTH = 8;

/** Visible blocks under `root`, in reading order. */
export function collectBlocks(root: Element): Element[] {
  const vw = window.innerWidth, vh = window.innerHeight;
  const blocks: { el: Element; top: number; left: number }[] = [];
  const visit = (parent: Element, depth: number) => {
    for (const el of Array.from(parent.children)) {
      if (blocks.length >= MAX_BLOCKS) return;
      if (el.matches(SKIP)) continue;
      const style = getComputedStyle(el);
      if (style.display === "contents") { visit(el, depth); continue; }
      if (style.display === "none" || style.visibility === "hidden" || Number(style.opacity) === 0) continue;
      const r = el.getBoundingClientRect();
      const visible = Math.max(0, Math.min(r.right, vw) - Math.max(r.left, 0)) * Math.max(0, Math.min(r.bottom, vh) - Math.max(r.top, 0));
      if (visible < 1) continue;
      if (depth < MAX_DEPTH && el.childElementCount > 0 && visible > vw * vh * CONTAINER_SHARE) visit(el, depth + 1);
      else blocks.push({ el, top: Math.round(r.top / 8), left: r.left });
    }
  };
  visit(root, 0);
  return blocks.sort((a, b) => a.top - b.top || a.left - b.left).map((b) => b.el);
}

const stagger = (count: number, budget: number, max: number) => Math.min(max, budget / Math.max(count - 1, 1));

export const EXIT_DURATION = 0.5;

/** Delay (seconds) before block `index` of `count` starts to leave. */
export const exitDelay = (index: number, count: number) => index * stagger(count, 0.3, 0.035);

/** Slides blocks out to the left. They stay hidden (fill: forwards) until the animations are cancelled. */
export function exitBlocks(blocks: Element[]): Animation[] {
  const distance = Math.min(180, window.innerWidth * 0.14);
  return blocks.map((el, i) =>
    el.animate([{ opacity: 0, translate: `${-distance}px 0`, filter: "blur(4px)" }], {
      duration: EXIT_DURATION * 1000,
      delay: exitDelay(i, blocks.length) * 1000,
      easing: "cubic-bezier(0.55, 0, 0.9, 0.35)",
      fill: "forwards",
    }),
  );
}

/**
 * Prepares blocks to slide in from the right. Returned paused on their first frame
 * (hidden), so the page can render under the veil and enter on cue with `play()`.
 * The end state is implicit: each block animates back to whatever its own styles say.
 */
export function enterBlocks(blocks: Element[]): Animation[] {
  const distance = Math.min(120, window.innerWidth * 0.09);
  const gap = stagger(blocks.length, 0.35, 0.04);
  return blocks.map((el, i) => {
    const animation = el.animate([{ opacity: 0, translate: `${distance}px 0`, filter: "blur(6px)", offset: 0 }], {
      duration: 750,
      delay: i * gap * 1000,
      easing: "cubic-bezier(0.16, 1, 0.3, 1)",
      fill: "backwards",
    });
    animation.pause();
    return animation;
  });
}
