// One clock drives every phase, so the passes cannot drift apart.
// All times are seconds since the intro started (the model is already loaded).

export const PHASES = {
  black: [0.0, 0.4],
  rush: [0.4, 2.4],
  assemble: [2.4, 4.2],
  solidify: [4.2, 5.0],
  reveal: [5.0, 6.5],
  handoff: [6.5, 7.3],
} as const satisfies Record<string, readonly [number, number]>;

export type PhaseName = keyof typeof PHASES;

export const TOTAL_DURATION = PHASES.handoff[1];

export const clamp01 = (x: number) => Math.min(1, Math.max(0, x));

/** 0 before the phase, 1 after it, linear in between. */
export function phaseProgress(t: number, phase: PhaseName): number {
  const [start, end] = PHASES[phase];
  return clamp01((t - start) / (end - start));
}

export function currentPhase(t: number): PhaseName {
  for (const name of Object.keys(PHASES) as PhaseName[]) {
    if (t < PHASES[name][1]) return name;
  }
  return "handoff";
}

export const easeInOutCubic = (x: number) =>
  x < 0.5 ? 4 * x * x * x : 1 - Math.pow(-2 * x + 2, 3) / 2;

export const easeOutCubic = (x: number) => 1 - Math.pow(1 - x, 3);

/** Uniform values for one frame. The shaders do the per-particle work. */
export interface FrameState {
  time: number;
  /** Raw 0..1 progress of each phase; the shaders apply per-particle delays and easing. */
  rush: number;
  assemble: number;
  /** Eased 0..1 cross-fade from particles to the lit mesh. */
  solid: number;
  /** Eased 0..1 wavefront radius progress. */
  reveal: number;
  /** Eased 0..1 fade of the whole canvas at the end. */
  handoff: number;
}

export function frameAt(t: number): FrameState {
  return {
    time: t,
    rush: phaseProgress(t, "rush"),
    assemble: phaseProgress(t, "assemble"),
    solid: easeInOutCubic(phaseProgress(t, "solidify")),
    reveal: easeInOutCubic(phaseProgress(t, "reveal")),
    handoff: easeOutCubic(phaseProgress(t, "handoff")),
  };
}
