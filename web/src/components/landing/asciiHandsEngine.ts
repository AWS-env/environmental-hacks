import { gsap } from "@/components/motion/gsap";
import { HANDS_CELL_ASPECT, HANDS_COLS, HANDS_LEVELS, HANDS_ROWS } from "./footerHandsArt";

/**
 * Two ASCII hands reaching toward each other (the footer's "Creation of Adam" motif).
 *
 * The silhouettes come from `footerHandsArt.ts`, a per-cell ink density traced from the reference
 * render, so the shapes match it glyph for glyph. Every inked cell becomes a glyph particle: denser
 * cells pick heavier characters and higher opacity. GSAP drives the entrance and idle breathing.
 *
 * Interaction:
 * - the cursor pushes glyphs aside and leaves a warm, scrambling trail behind it;
 * - bringing the cursor toward the gap makes the hands lean in, and a spark flickers between them;
 * - a click sends a shockwave through the glyphs.
 */

/** Light to heavy, matching the character mix of the reference. */
const RAMP = ".'`,^:;~-_!i|lI1/\\()[]{}<>?+rtfjcvunxzJLUYXZCmwqOQ0";
const SCRAMBLE = "{}[]<>?/\\|+rtfjcvunxzJLUYXZCmwqOQ0";
const SPARK = "*+'`.^~";
/** Below this the glyphs turn to noise; narrower stages crop the art around the gap instead. */
const MIN_CELL = 7;
/** Glyph size as a share of the cell pitch. */
const FONT_SCALE = 0.9;
/** How much later the fingertips land than the shoulder, as a share of the entrance. */
const DELAY_SPREAD = 0.45;
const DELAY_JITTER = 0.08;
const REPEL_RADIUS = 84;
const TRAIL_RADIUS = 120;
/** How far each fingertip travels toward the other at full lean, in cells. */
const REACH_CELLS = 3;
const RIPPLE_SPEED = 720;
const RIPPLE_BAND = 46;
const MAX_RIPPLES = 4;
const ENTRANCE_SECONDS = 2.8;
const MAX_FPS = 60;

function mulberry32(seed: number) {
  return () => {
    seed = (seed + 0x6d2b79f5) | 0;
    let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

type Cloud = {
  count: number;
  cell: number;
  hx: Float32Array;
  hy: Float32Array;
  ox: Float32Array;
  oy: Float32Array;
  vx: Float32Array;
  vy: Float32Array;
  /** -1 for the left hand, 1 for the right. */
  side: Int8Array;
  /** 0 at the screen edge, 1 at the fingertip; scales the lean. */
  reach: Float32Array;
  base: Uint8Array;
  glyph: Uint8Array;
  alpha: Float32Array;
  /** Cursor and shockwave energy; brightens and scrambles the glyph, then decays. */
  heat: Float32Array;
  delay: Float32Array;
  /** Per-particle random in [0, 1): entrance travel and shimmer phase. */
  rnd: Float32Array;
  /** Midpoint between the two fingertips, where the spark lives. */
  gap: { x: number; y: number; width: number };
};

function buildCloud(w: number): Cloud {
  const cell = Math.max(w / HANDS_COLS, MIN_CELL);
  const cellH = cell * HANDS_CELL_ASPECT;
  // Centred, so a stage narrower than the art crops both shoulders evenly and keeps the gap.
  const originX = (w - HANDS_COLS * cell) / 2;
  const mid = HANDS_COLS / 2;
  const rand = mulberry32(0x5eed);

  // The traced ink is noisy (an `i` and a `Q` in the same solid patch differ a lot), so each
  // cell's tone blends its own ink with its neighbourhood's.
  const rows = HANDS_ROWS.length;
  const ink = new Float32Array(rows * HANDS_COLS);
  HANDS_ROWS.forEach((line, row) => {
    for (let col = 0; col < line.length; col++) {
      ink[row * HANDS_COLS + col] = (HANDS_LEVELS.indexOf(line[col]) + 1) / HANDS_LEVELS.length;
    }
  });

  const cells: { col: number; row: number; d: number }[] = [];
  for (let row = 0; row < rows; row++) {
    for (let col = 0; col < HANDS_COLS; col++) {
      const own = ink[row * HANDS_COLS + col];
      if (own === 0) continue;
      const x = originX + (col + 0.5) * cell;
      if (x < -cell || x > w + cell) continue;
      let sum = 0;
      for (let dy = -1; dy <= 1; dy++) {
        for (let dx = -1; dx <= 1; dx++) {
          const r = row + dy;
          const c = col + dx;
          if (r >= 0 && r < rows && c >= 0 && c < HANDS_COLS) sum += ink[r * HANDS_COLS + c];
        }
      }
      cells.push({ col, row, d: Math.min(1, 0.6 * own + 0.4 * (sum / 9) * 1.25) });
    }
  }

  const n = cells.length;
  const cloud: Cloud = {
    count: n,
    cell,
    hx: new Float32Array(n),
    hy: new Float32Array(n),
    ox: new Float32Array(n),
    oy: new Float32Array(n),
    vx: new Float32Array(n),
    vy: new Float32Array(n),
    side: new Int8Array(n),
    reach: new Float32Array(n),
    base: new Uint8Array(n),
    glyph: new Uint8Array(n),
    alpha: new Float32Array(n),
    heat: new Float32Array(n),
    delay: new Float32Array(n),
    rnd: new Float32Array(n),
    gap: { x: w / 2, y: 0, width: cell * 8 },
  };

  let leftTip = { x: -Infinity, y: 0 };
  let rightTip = { x: Infinity, y: 0 };

  cells.forEach(({ col, row, d }, i) => {
    const x = originX + (col + 0.5) * cell;
    const y = (row + 0.5) * cellH;
    const side = col < mid ? -1 : 1;
    const fromEdge = Math.min(1, side < 0 ? col / mid : (HANDS_COLS - col) / mid);
    const tone = Math.pow(d, 0.7) * (RAMP.length - 1) + (rand() - 0.5) * 5;
    const glyph = Math.min(RAMP.length - 1, Math.max(0, Math.round(tone)));

    cloud.hx[i] = x;
    cloud.hy[i] = y;
    cloud.side[i] = side;
    cloud.reach[i] = fromEdge * fromEdge;
    cloud.base[i] = glyph;
    cloud.glyph[i] = glyph;
    cloud.alpha[i] = 0.38 + 0.62 * Math.pow(d, 0.55);
    cloud.delay[i] = fromEdge * DELAY_SPREAD + rand() * DELAY_JITTER;
    cloud.rnd[i] = rand();

    if (d > 0.25) {
      if (side < 0 && x > leftTip.x) leftTip = { x, y };
      if (side > 0 && x < rightTip.x) rightTip = { x, y };
    }
  });

  if (Number.isFinite(leftTip.x) && Number.isFinite(rightTip.x)) {
    cloud.gap = {
      x: (leftTip.x + rightTip.x) / 2,
      y: (leftTip.y + rightTip.y) / 2,
      width: Math.max(cell * 4, rightTip.x - leftTip.x - cell * 2 * REACH_CELLS),
    };
  }
  return cloud;
}

export type FooterHands = { destroy: () => void };

export function createFooterHands(stage: HTMLElement, canvas: HTMLCanvasElement, reduceMotion: boolean): FooterHands {
  const ctx = canvas.getContext("2d")!;
  const ease = gsap.parseEase("power3.out");
  const entrance = { left: reduceMotion ? 1 : 0, right: reduceMotion ? 1 : 0, breath: 0.5 };
  const pointer = { x: 0, y: 0, active: false };
  const ripples: { x: number; y: number; born: number }[] = [];

  let w = 0;
  let h = 0;
  let dpr = 1;
  let cloud = buildCloud(1);
  let color = "#e8604a";
  let fontFamily = "monospace";
  let visible = false;
  let played = false;
  let last = 0;
  let now = 0;
  let lean = 0;

  const readTheme = () => {
    const style = getComputedStyle(canvas);
    color = style.getPropertyValue("--footer-glyph").trim() || color;
    const mono = style.getPropertyValue("--font-geist-mono").trim();
    fontFamily = mono ? `${mono}, monospace` : "monospace";
  };

  const resize = () => {
    const rect = stage.getBoundingClientRect();
    if (rect.width < 2 || rect.height < 2) return;
    w = rect.width;
    h = rect.height;
    dpr = Math.min(window.devicePixelRatio || 1, 2);
    canvas.width = Math.round(w * dpr);
    canvas.height = Math.round(h * dpr);
    cloud = buildCloud(w);
    readTheme();
    draw(now, 0);
  };

  const draw = (time: number, dt: number) => {
    const c = cloud;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, h);
    ctx.font = `500 ${c.cell * FONT_SCALE}px ${fontFamily}`;
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.fillStyle = color;

    const span = 1 - DELAY_SPREAD - DELAY_JITTER;
    const sway = (entrance.breath - 0.5) * c.cell * 1.4;
    const travel = w * 0.42;
    const repel2 = REPEL_RADIUS * REPEL_RADIUS;
    const trail2 = TRAIL_RADIUS * TRAIL_RADIUS;
    const reach = REACH_CELLS * c.cell;

    if (dt > 0) {
      // The hands lean in as the cursor nears the gap between them.
      let target = 0;
      if (pointer.active) {
        const dist = Math.hypot(pointer.x - c.gap.x, (pointer.y - c.gap.y) * 1.6);
        target = Math.max(0, 1 - dist / (w * 0.28)) ** 1.5;
      }
      lean += (target - lean) * Math.min(1, dt * 5);
      for (let r = ripples.length - 1; r >= 0; r--) {
        if ((time - ripples[r].born) * RIPPLE_SPEED > Math.hypot(w, h)) ripples.splice(r, 1);
      }
    }
    const cool = Math.exp(-dt * 1.8);

    for (let i = 0; i < c.count; i++) {
      const side = c.side[i];
      const raw = ((side < 0 ? entrance.left : entrance.right) - c.delay[i]) / span;
      if (raw <= 0) continue;
      const e = raw >= 1 ? 1 : ease(raw);
      const inv = 1 - e;
      const rnd = c.rnd[i];
      const pull = c.reach[i] * (lean * reach - sway);

      const x = c.hx[i] + side * inv * inv * travel * (0.7 + rnd * 0.6) - side * pull + Math.sin(time * 0.9 + rnd * 40) * 0.6;
      const y = c.hy[i] + (rnd - 0.5) * 70 * inv + Math.cos(time * 0.7 + rnd * 31) * 0.6 + sway * 0.25 * c.reach[i];

      if (dt > 0) {
        const px = x + c.ox[i];
        const py = y + c.oy[i];
        let heat = c.heat[i] * cool;

        if (pointer.active) {
          const dx = px - pointer.x;
          const dy = py - pointer.y;
          const d2 = dx * dx + dy * dy;
          if (d2 < trail2) {
            const d = Math.sqrt(d2);
            heat = Math.max(heat, 1 - d / TRAIL_RADIUS);
            if (d2 < repel2 && d > 0.1) {
              const push = (1 - d / REPEL_RADIUS) ** 2 * 900 * dt;
              c.vx[i] += (dx / d) * push;
              c.vy[i] += (dy / d) * push;
            }
          }
        }

        for (const ripple of ripples) {
          const dx = px - ripple.x;
          const dy = py - ripple.y;
          const d = Math.hypot(dx, dy);
          const band = 1 - Math.abs(d - (time - ripple.born) * RIPPLE_SPEED) / RIPPLE_BAND;
          if (band > 0 && d > 0.1) {
            const fade = Math.max(0.25, 1 - d / Math.hypot(w, h));
            const push = band * fade * 2600 * dt;
            c.vx[i] += (dx / d) * push;
            c.vy[i] += (dy / d) * push;
            heat = Math.max(heat, band * fade);
          }
        }

        c.vx[i] += (-c.ox[i] * 16 - c.vx[i] * 5) * dt;
        c.vy[i] += (-c.oy[i] * 16 - c.vy[i] * 5) * dt;
        c.ox[i] += c.vx[i] * dt;
        c.oy[i] += c.vy[i] * dt;
        c.heat[i] = heat;

        // Hot glyphs scramble; cool ones crawl around their own density, so the surface still breathes.
        if (heat > 0.08 && Math.random() < heat * 0.5) {
          c.glyph[i] = RAMP.indexOf(SCRAMBLE[(Math.random() * SCRAMBLE.length) | 0]);
        } else if (Math.random() < (c.glyph[i] === c.base[i] ? 0.0025 : 0.04)) {
          const g = c.base[i] + Math.round((Math.random() - 0.5) * 4);
          c.glyph[i] = Math.min(RAMP.length - 1, Math.max(0, g));
        }
      }

      const shimmer = 0.88 + 0.12 * Math.sin(time * 1.6 + rnd * 20);
      const a = Math.min(1, c.alpha[i] * shimmer + c.heat[i] * 0.7) * e;
      if (a < 0.01) continue;
      ctx.globalAlpha = a;
      ctx.fillText(RAMP[c.glyph[i]], x + c.ox[i], y + c.oy[i]);
    }

    // A spark flickers in the gap once the hands are close.
    const spark = Math.min(entrance.left, entrance.right) * Math.max(0, lean - 0.35) / 0.65;
    if (spark > 0.01) {
      const frame = Math.floor(time * 14);
      for (let k = 0; k < 28; k++) {
        const r = mulberry32(frame * 977 + k);
        const u = r() - 0.5;
        ctx.globalAlpha = Math.min(1, spark * (0.55 + 0.6 * r()) * (1 - Math.abs(u)));
        ctx.fillText(
          SPARK[(r() * SPARK.length) | 0],
          c.gap.x + u * c.gap.width * (0.4 + 0.6 * spark),
          c.gap.y + (r() - 0.5) * c.cell * 5 * spark * (1 - Math.abs(u)),
        );
      }
    }
    ctx.globalAlpha = 1;
  };

  const tick = (time: number, deltaMs: number) => {
    now = time;
    if (!visible) return;
    if (time - last < 1 / MAX_FPS) return;
    last = time;
    draw(time, Math.min(deltaMs / 1000, 0.05));
  };

  const onMove = (event: PointerEvent) => {
    if (!visible) return;
    const rect = canvas.getBoundingClientRect();
    pointer.x = event.clientX - rect.left;
    pointer.y = event.clientY - rect.top;
    pointer.active = pointer.y > -REPEL_RADIUS && pointer.y < rect.height + REPEL_RADIUS;
  };
  const onLeave = () => {
    pointer.active = false;
  };
  const onDown = (event: PointerEvent) => {
    if (!visible) return;
    const rect = canvas.getBoundingClientRect();
    const x = event.clientX - rect.left;
    const y = event.clientY - rect.top;
    if (x < 0 || y < 0 || x > rect.width || y > rect.height) return;
    // Links in the copy keep their click; the shockwave is for the open canvas.
    if ((event.target as Element | null)?.closest("a, button")) return;
    if (ripples.length >= MAX_RIPPLES) ripples.shift();
    ripples.push({ x, y, born: now });
  };

  const intro = gsap.timeline({ paused: true });
  intro
    .to(entrance, { left: 1, duration: ENTRANCE_SECONDS, ease: "none" }, 0)
    .to(entrance, { right: 1, duration: ENTRANCE_SECONDS, ease: "none" }, 0.35);
  const breathing = gsap.to(entrance, { breath: 1, duration: 5.5, ease: "sine.inOut", yoyo: true, repeat: -1, paused: true });

  const observer = new IntersectionObserver(
    ([entry]) => {
      visible = entry.isIntersecting;
      if (!visible) {
        breathing.pause();
        return;
      }
      if (reduceMotion) {
        draw(0, 0);
        return;
      }
      breathing.play();
      if (!played) {
        played = true;
        intro.play();
      }
    },
    { threshold: 0.2 },
  );

  const resizeObserver = new ResizeObserver(() => resize());
  const themeObserver = new MutationObserver(() => {
    readTheme();
    if (reduceMotion) draw(0, 0);
  });

  resize();
  resizeObserver.observe(stage);
  observer.observe(stage);
  themeObserver.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme", "class"] });
  if (!reduceMotion) {
    gsap.ticker.add(tick);
    window.addEventListener("pointermove", onMove, { passive: true });
    window.addEventListener("pointerdown", onDown, { passive: true });
    document.documentElement.addEventListener("pointerleave", onLeave);
  }
  // The mono font may still be loading on first paint; redraw once it lands.
  document.fonts?.ready.then(() => {
    readTheme();
    draw(now, 0);
  });

  return {
    destroy() {
      gsap.ticker.remove(tick);
      intro.kill();
      breathing.kill();
      observer.disconnect();
      resizeObserver.disconnect();
      themeObserver.disconnect();
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerdown", onDown);
      document.documentElement.removeEventListener("pointerleave", onLeave);
    },
  };
}
