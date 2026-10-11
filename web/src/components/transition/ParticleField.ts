/**
 * Canvas particle veil for route transitions. Three acts, all on one pool of particles:
 * - drift:   dust shed by the departing page floats in a flow field (`start`)
 * - form:    particles fly in to type out the destination route, with a blinking caret (`type`)
 * - scatter: everything bursts outward and fades (`scatter`)
 *
 * Plain 2D canvas: a few thousand 1–3px squares, drawn in a handful of batched paths per frame.
 */

/** Where dust comes from: a departing element's box, and when (seconds) it starts to leave. */
export interface DustSource {
  rect: DOMRect;
  delay: number;
}

export interface FieldColors {
  base: string;
  /** Flash as each typed particle lands. */
  glow: string;
}

// Typing rhythm, in seconds.
const CARET_LEAD = 0.22, CHAR_GAP = 0.085, CHAR_SPREAD = 0.09, SETTLE = 0.5, HOLD = 0.4;
export const SCATTER_S = 0.85;
// Spring that pulls typed particles home: stiffness and damping (slightly underdamped, so letters land with a little bounce).
const STIFFNESS = 150, DAMPING = 19;
const ALPHA_LEVELS = 8;

const enum Role { Free, Text, Caret }

const clamp = (v: number, lo: number, hi: number) => Math.min(Math.max(v, lo), hi);

export class ParticleField {
  private readonly ctx: CanvasRenderingContext2D;
  private readonly n: number;
  private readonly x: Float32Array; private readonly y: Float32Array;
  private readonly vx: Float32Array; private readonly vy: Float32Array;
  private readonly tx: Float32Array; private readonly ty: Float32Array;
  private readonly appearAt: Float32Array; private readonly seekAt: Float32Array;
  private readonly alpha: Float32Array; private readonly peak: Float32Array;
  private readonly glow: Float32Array; private readonly size: Float32Array;
  private readonly role: Uint8Array; private readonly landed: Uint8Array; private readonly bucket: Uint8Array;
  private w = 0; private h = 0; private dpr = 1;
  private time = 0; private last = 0; private raf = 0;
  private mode: "drift" | "form" | "scatter" = "drift";
  private seed = Math.random() * 100;
  private center = { x: 0, y: 0 };
  private caretX = 0; private caretTo = 0; private caretStops: { at: number; x: number }[] = []; private blinkFrom = Infinity;
  private waiters: { at: number; resolve: () => void }[] = [];

  constructor(private readonly canvas: HTMLCanvasElement, private readonly colors: FieldColors) {
    const ctx = canvas.getContext("2d");
    if (!ctx) throw new Error("2D canvas unavailable");
    this.ctx = ctx;
    this.n = window.innerWidth < 768 ? 1400 : 3000;
    const f = () => new Float32Array(this.n);
    this.x = f(); this.y = f(); this.vx = f(); this.vy = f(); this.tx = f(); this.ty = f();
    this.appearAt = f(); this.seekAt = f(); this.alpha = f(); this.peak = f(); this.glow = f(); this.size = f();
    this.role = new Uint8Array(this.n); this.landed = new Uint8Array(this.n); this.bucket = new Uint8Array(this.n);
    this.resize();
    window.addEventListener("resize", this.resize);
  }

  /** Seeds dust over the departing elements (most particles) and across the screen (the rest), then starts drawing. */
  start(sources: DustSource[]) {
    const { n, w, h } = this;
    const visible = sources.map(({ rect: r }) =>
      Math.max(0, Math.min(r.right, w) - Math.max(r.left, 0)) * Math.max(0, Math.min(r.bottom, h) - Math.max(r.top, 0)));
    const total = visible.reduce((a, b) => a + b, 0);
    const shed = total > 0 ? Math.round(n * 0.7) : 0;
    let i = 0;
    sources.forEach(({ rect: r, delay }, k) => {
      const count = Math.round((shed * visible[k]) / total);
      const left = Math.max(r.left, 0), top = Math.max(r.top, 0);
      const rw = Math.min(r.right, w) - left, rh = Math.min(r.bottom, h) - top;
      for (let c = 0; c < count && i < n; c++, i++) {
        this.spawn(i, left + Math.random() * rw, top + Math.random() * rh, delay + Math.random() * 0.35);
        // Shed leftward, in the wake of the element.
        this.vx[i] = -(120 + Math.random() * 380);
        this.vy[i] = (Math.random() - 0.5) * 60;
      }
    });
    for (; i < n; i++) this.spawn(i, Math.random() * w, Math.random() * h, 0.15 + Math.random() * 0.6);
    this.last = 0;
    this.raf = requestAnimationFrame(this.loop);
  }

  /** Types `text` out of particles, centred on screen. Resolves once it has been held on screen for a beat. */
  async type(text: string, fontFamily: string): Promise<void> {
    const { w, h, n } = this;
    const fontSize = clamp(Math.min((w * 0.8) / (text.length * 0.62 + 0.3), h * 0.3), 44, 176);
    const font = `500 ${fontSize}px ${fontFamily}`;
    // The mono face may not be used on the page yet; don't hold the transition long for it.
    await Promise.race([document.fonts.load(font).catch(() => undefined), new Promise((r) => setTimeout(r, 400))]);

    const off = document.createElement("canvas");
    off.width = w; off.height = h;
    const o = off.getContext("2d", { willReadFrequently: true });
    if (!o) return this.until(this.time);
    o.font = font;
    const prefix = [0];
    for (let c = 1; c <= text.length; c++) prefix.push(o.measureText(text.slice(0, c)).width);
    const caretGap = fontSize * 0.08, caretW = Math.max(3, fontSize * 0.07);
    const left = (w - (prefix[text.length] + caretGap + caretW)) / 2;
    const baseline = h / 2 + fontSize * 0.34;
    this.center = { x: w / 2, y: baseline - fontSize * 0.36 };

    // Rasterise each character on its own, so every point knows which keystroke it belongs to.
    const glyphs = [...text].map((ch, c) => {
      o.clearRect(0, 0, w, h);
      o.fillText(ch, left + prefix[c], baseline);
      const x0 = clamp(Math.floor(left + prefix[c] - fontSize * 0.1), 0, w - 1);
      const y0 = clamp(Math.floor(baseline - fontSize), 0, h - 1);
      const bw = clamp(Math.ceil(prefix[c + 1] - prefix[c] + fontSize * 0.2), 1, w - x0);
      const bh = clamp(Math.ceil(fontSize * 1.3), 1, h - y0);
      return { x0, y0, bw, bh, data: o.getImageData(x0, y0, bw, bh).data };
    });
    const sample = (gap: number) => {
      const pts: { x: number; y: number; c: number }[] = [];
      glyphs.forEach(({ x0, y0, bw, bh, data }, c) => {
        for (let yy = 0; yy < bh; yy += gap)
          for (let xx = 0; xx < bw; xx += gap)
            if (data[(yy * bw + xx) * 4 + 3] > 110)
              pts.push({ x: x0 + xx + (Math.random() - 0.5) * gap * 0.5, y: y0 + yy + (Math.random() - 0.5) * gap * 0.5, c });
      });
      return pts;
    };
    // Pick the sampling gap so the text uses most of the pool without exceeding it.
    const budget = n * 0.8;
    let gap = Math.max(2, Math.round(fontSize / 42));
    let pts = sample(gap);
    while (pts.length > budget && gap < 14) pts = sample(++gap);
    while (pts.length < budget * 0.35 && gap > 2) {
      const denser = sample(gap - 1);
      if (denser.length > budget) break;
      pts = denser; gap--;
    }
    const caret: { x: number; y: number }[] = [];
    const caretStep = Math.min(gap, caretW / 2);
    for (let yy = baseline - fontSize * 0.76; yy <= baseline + fontSize * 0.06; yy += gap)
      for (let xx = 0; xx <= caretW; xx += caretStep) caret.push({ x: xx, y: yy });

    // Random assignment: every letter gathers particles from all over the screen.
    const order = Array.from({ length: n }, (_, k) => k);
    for (let k = n - 1; k > 0; k--) { const j = Math.floor(Math.random() * (k + 1)); [order[k], order[j]] = [order[j], order[k]]; }
    const t0 = this.time;
    const assign = (k: number, role: Role, x: number, y: number, at: number) => {
      const i = order[k];
      this.role[i] = role; this.tx[i] = x; this.ty[i] = y; this.seekAt[i] = at; this.landed[i] = 0;
      if (this.appearAt[i] > t0) this.appearAt[i] = t0;
    };
    pts.forEach((p, k) => assign(k, Role.Text, p.x, p.y, t0 + CARET_LEAD + p.c * CHAR_GAP + Math.random() * CHAR_SPREAD));
    caret.slice(0, n - pts.length).forEach((p, k) => assign(pts.length + k, Role.Caret, p.x, p.y, t0 + Math.random() * 0.12));

    this.caretX = this.caretTo = left;
    this.caretStops = [...text].map((_, c) => ({ at: t0 + CARET_LEAD + c * CHAR_GAP, x: left + prefix[c + 1] + caretGap }));
    const typed = t0 + CARET_LEAD + (text.length - 1) * CHAR_GAP + SETTLE;
    this.blinkFrom = typed;
    this.mode = "form";
    return this.until(typed + HOLD);
  }

  /** Bursts every particle away from the text and fades the field out. */
  scatter(): Promise<void> {
    this.mode = "scatter";
    const { x: cx, y: cy } = this.center.x ? this.center : { x: this.w / 2, y: this.h / 2 };
    for (let i = 0; i < this.n; i++) {
      if (this.appearAt[i] > this.time) { this.appearAt[i] = Infinity; continue; }
      const dx = this.x[i] - cx, dy = this.y[i] - cy;
      const angle = Math.atan2(dy, dx) + (Math.random() - 0.5) * 0.6;
      const speed = this.role[i] === Role.Free ? 120 + Math.random() * 360 : 380 + Math.random() * 900;
      this.vx[i] = this.vx[i] * 0.3 + Math.cos(angle) * speed;
      this.vy[i] = this.vy[i] * 0.3 + Math.sin(angle) * speed;
    }
    return this.until(this.time + SCATTER_S);
  }

  destroy() {
    cancelAnimationFrame(this.raf);
    window.removeEventListener("resize", this.resize);
    this.ctx.clearRect(0, 0, this.w, this.h);
    // Never leave the transition awaiting a frame that will not come.
    this.waiters.forEach((wt) => wt.resolve());
    this.waiters = [];
  }

  private spawn(i: number, x: number, y: number, at: number) {
    this.x[i] = x; this.y[i] = y; this.vx[i] = (Math.random() - 0.5) * 20; this.vy[i] = (Math.random() - 0.5) * 20;
    this.appearAt[i] = at; this.seekAt[i] = Infinity; this.role[i] = Role.Free;
    this.alpha[i] = 0; this.peak[i] = 0.3 + Math.random() * 0.5; this.glow[i] = 0; this.size[i] = 1.1 + Math.random() * 1.3;
  }

  private until(at: number) {
    return new Promise<void>((resolve) => this.waiters.push({ at, resolve }));
  }

  private resize = () => {
    this.w = window.innerWidth; this.h = window.innerHeight;
    this.dpr = Math.min(window.devicePixelRatio || 1, 2);
    this.canvas.width = Math.round(this.w * this.dpr); this.canvas.height = Math.round(this.h * this.dpr);
    this.ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
  };

  private loop = (now: number) => {
    this.raf = requestAnimationFrame(this.loop);
    const dt = this.last ? Math.min((now - this.last) / 1000, 1 / 20) : 1 / 60;
    this.last = now;
    this.time += dt;
    this.step(dt);
    this.draw();
    this.waiters = this.waiters.filter((wt) => (this.time >= wt.at ? (wt.resolve(), false) : true));
  };

  private step(dt: number) {
    const { n, w, h, time: t, seed } = this;
    while (this.caretStops.length && t >= this.caretStops[0].at) this.caretTo = this.caretStops.shift()!.x;
    this.caretX += (this.caretTo - this.caretX) * (1 - Math.exp(-dt * 22));
    const caretOn = t < this.blinkFrom || Math.floor((t - this.blinkFrom) * 2.6) % 2 === 0;
    const drag = Math.exp(-dt * 1.4), fade = 1 - Math.exp(-dt * 6), cool = Math.exp(-dt * 2.8);
    for (let i = 0; i < n; i++) {
      if (t < this.appearAt[i]) continue;
      let x = this.x[i], y = this.y[i], vx = this.vx[i], vy = this.vy[i];
      // Flow field: a slowly turning wind that keeps the dust wandering.
      const a = (Math.sin(x * 0.0021 + t * 0.13 + seed) + Math.sin(y * 0.0027 - t * 0.11) + Math.sin((x + y) * 0.0013 + t * 0.07)) * 2.1;
      const role = this.role[i];
      const seeking = role !== Role.Free && t >= this.seekAt[i] && this.mode === "form";
      let target: number;
      if (this.mode === "scatter") {
        vx = (vx + Math.cos(a) * 60 * dt) * Math.exp(-dt * 1.6);
        vy = (vy + Math.sin(a) * 60 * dt) * Math.exp(-dt * 1.6);
        target = 0;
        this.alpha[i] *= Math.exp(-dt * 3.6);
      } else if (seeking) {
        // Landed particles shimmer a fraction of a pixel around their spot.
        const gx = this.tx[i] + (role === Role.Caret ? this.caretX : 0) + Math.sin(t * 3 + i) * 0.5;
        const gy = this.ty[i] + Math.cos(t * 2.6 + i * 1.3) * 0.5;
        vx += ((gx - x) * STIFFNESS - vx * DAMPING) * dt;
        vy += ((gy - y) * STIFFNESS - vy * DAMPING) * dt;
        if (!this.landed[i] && Math.abs(gx - x) + Math.abs(gy - y) < 3) { this.landed[i] = 1; if (role === Role.Text) this.glow[i] = 1; }
        target = role === Role.Caret && !caretOn ? 0.06 : 0.95;
      } else {
        vx = (vx + Math.cos(a) * 60 * dt) * drag;
        vy = (vy + Math.sin(a) * 60 * dt) * drag;
        // Bystanders dim while the route is written, so the text reads.
        target = this.mode === "form" ? this.peak[i] * 0.3 : this.peak[i];
      }
      x += vx * dt; y += vy * dt;
      if (!seeking && this.mode !== "scatter") {
        if (x < -20) x += w + 40; else if (x > w + 20) x -= w + 40;
        if (y < -20) y += h + 40; else if (y > h + 20) y -= h + 40;
      }
      if (this.mode !== "scatter") this.alpha[i] += (target - this.alpha[i]) * fade;
      this.glow[i] *= cool;
      this.x[i] = x; this.y[i] = y; this.vx[i] = vx; this.vy[i] = vy;
    }
  }

  private draw() {
    const { ctx, n, w, h } = this;
    ctx.clearRect(0, 0, w, h);
    // Bucket by colour and quantised alpha, so a frame is ~16 fills rather than thousands of style changes.
    for (let i = 0; i < n; i++) {
      const a = this.time < this.appearAt[i] ? 0 : this.alpha[i];
      this.bucket[i] = a < 0.02 ? 255 : (this.glow[i] > 0.3 ? ALPHA_LEVELS : 0) + Math.min(ALPHA_LEVELS - 1, Math.floor(a * ALPHA_LEVELS));
    }
    for (let b = 0; b < ALPHA_LEVELS * 2; b++) {
      ctx.beginPath();
      let any = false;
      for (let i = 0; i < n; i++) {
        if (this.bucket[i] !== b) continue;
        const s = this.size[i] * (1 + this.glow[i] * 1.2);
        ctx.rect(this.x[i] - s / 2, this.y[i] - s / 2, s, s);
        any = true;
      }
      if (!any) continue;
      ctx.globalAlpha = ((b % ALPHA_LEVELS) + 0.5) / ALPHA_LEVELS;
      ctx.fillStyle = b >= ALPHA_LEVELS ? this.colors.glow : this.colors.base;
      ctx.fill();
    }
    ctx.globalAlpha = 1;
  }
}
