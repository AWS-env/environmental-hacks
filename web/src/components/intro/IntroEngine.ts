import {
  AddEquation,
  BufferAttribute,
  BufferGeometry,
  Color,
  CustomBlending,
  DirectionalLight,
  Group,
  HemisphereLight,
  LinearSRGBColorSpace,
  Mesh,
  NoBlending,
  OneFactor,
  OneMinusSrcAlphaFactor,
  PerspectiveCamera,
  PlaneGeometry,
  Points,
  ACESFilmicToneMapping,
  Scene,
  ShaderMaterial,
  Texture,
  Vector3,
  WebGLRenderer,
} from "three";
import type { IUniform, Material, MeshStandardMaterial } from "three";
import { GLTFLoader } from "three/examples/jsm/loaders/GLTFLoader.js";
import { MeshoptDecoder } from "three/examples/jsm/libs/meshopt_decoder.module.js";
import type { Theme } from "@/components/theme/boot";
import { MODEL_URL } from "./config";
import { MOTE_FRACTION, normaliseModel, sampleModel } from "./sampleModel";
import type { ModelBounds } from "./sampleModel";
import { particlesFragment, particlesVertex } from "./shaders/particles";
import { revealFragment, revealVertex } from "./shaders/reveal";
import { PHASES, TOTAL_DURATION, currentPhase, frameAt } from "./timeline";
import type { PhaseName } from "./timeline";

export type EndReason = "finished" | "slow" | "error";

export interface EngineCallbacks {
  /** First frame is on screen; the cover underneath can go. */
  onReady(): void;
  onPhase(phase: PhaseName): void;
  onEnd(reason: EndReason): void;
}

export interface EngineOptions {
  /** Keep running past the end and never report "finished" (debug scrubbing). */
  hold: boolean;
  freezeAt: number | null;
  /** Skip the animation and start on the settled tree. */
  ambient: boolean;
  /** Draw the settled tree once and never animate it (reduced motion). */
  still: boolean;
  /** Give up if the model is not loaded and compiled within this many ms. */
  loadTimeoutMs: number | null;
}

const FOV = 35;
// Brand colours from the dashboard theme, as display (sRGB) values.
const RIM_A = new Color("#34d399");
const RIM_B = new Color("#fcd34d");
const WASH = new Color("#10b981");

/** How the tree and its motes sit on each page theme's backdrop. */
interface Look {
  exposure: number;
  /** Display (sRGB) colour the lit tree is blended toward, and by how much. */
  haze: Color;
  hazeAmount: number;
  moteColor: Color;
  /** Extra size for drifting motes (painted motes need more body than glowing ones to read). */
  moteScale: number;
  /** Particle opacity once the page is revealed: 0 adds light, 1 paints over (see the particle shader). */
  moteCover: number;
}

const LOOKS: Record<Theme, Look> = {
  // Night: fireflies, light added to the dark page.
  dark: {
    exposure: 1.4,
    haze: new Color(0, 0, 0),
    hazeAmount: 0,
    moteColor: new Color("#fde68a").convertSRGBToLinear(),
    moteScale: 1,
    moteCover: 0,
  },
  // Day: the motes are painted gold over the sky, a little larger to read on it, and an airy
  // warm haze lightens the tree like distance does outdoors.
  light: {
    exposure: 1.7,
    haze: new Color().setStyle("#ece8cf", LinearSRGBColorSpace),
    hazeAmount: 0.14,
    moteColor: new Color("#fde68a").convertSRGBToLinear(),
    moteScale: 1.7,
    moteCover: 1,
  },
};

// The model download is shared across strict-mode remounts; parsing is not,
// because each mount owns (and disposes) its own GPU resources.
let modelBuffer: Promise<ArrayBuffer> | null = null;
function fetchModel(): Promise<ArrayBuffer> {
  modelBuffer ??= fetch(MODEL_URL, { credentials: "same-origin" }).then((res) => {
    if (!res.ok) throw new Error(`model ${res.status}`);
    return res.arrayBuffer();
  });
  modelBuffer.catch(() => {
    modelBuffer = null;
  });
  return modelBuffer;
}

interface DeviceTier {
  particles: number;
  maxPixelRatio: number;
}

function deviceTier(): DeviceTier {
  const small = Math.min(window.screen.width, window.screen.height) < 700;
  const cores = navigator.hardwareConcurrency ?? 4;
  if (small) return { particles: 20000, maxPixelRatio: 1 };
  if (cores <= 4) return { particles: 35000, maxPixelRatio: 1.25 };
  return { particles: 60000, maxPixelRatio: 1.5 };
}

function withTimeout<T>(promise: Promise<T>, ms: number | null): Promise<T> {
  if (ms === null) return promise;
  let timer: ReturnType<typeof setTimeout>;
  const timeout = new Promise<never>((_, reject) => {
    timer = setTimeout(() => reject(new Error("intro load timeout")), ms);
  });
  return Promise.race([promise, timeout]).finally(() => clearTimeout(timer));
}

interface TreeUniforms {
  fade: IUniform<number>;
  haze: IUniform<Color>;
  hazeAmount: IUniform<number>;
}

/** Adds a uniform fade and the theme's haze to a stock material without replacing its lighting. */
// It stays in the blended pass even when fully faded in: leaf edges have partial
// alpha, and writing that unblended would punch see-through holes in the backdrop.
// The haze is mixed in after tone mapping and colour conversion, so it is a display colour.
function makeFadeable(material: MeshStandardMaterial, uniforms: TreeUniforms) {
  material.transparent = true;
  material.onBeforeCompile = (shader) => {
    shader.uniforms.uFade = uniforms.fade;
    shader.uniforms.uHaze = uniforms.haze;
    shader.uniforms.uHazeAmount = uniforms.hazeAmount;
    shader.fragmentShader = shader.fragmentShader
      .replace("void main() {", "uniform float uFade;\nuniform vec3 uHaze;\nuniform float uHazeAmount;\nvoid main() {")
      .replace(
        "#include <dithering_fragment>",
        "#include <dithering_fragment>\n\tgl_FragColor.rgb = mix(gl_FragColor.rgb, uHaze, uHazeAmount);\n\tgl_FragColor.a *= uFade;",
      );
  };
}

/**
 * Plays the intro timeline, then stays "settled": the solid tree keeps turning
 * on the page, with motes drifting off it, until dispose().
 */
export class IntroEngine {
  private canvas: HTMLCanvasElement;
  private renderer: WebGLRenderer;
  private scene = new Scene();
  private camera = new PerspectiveCamera(FOV, 1, 0.01, 50);
  private group = new Group();
  private tree: Group | null = null;
  private points: Points<BufferGeometry, ShaderMaterial> | null = null;
  private reveal: Mesh<PlaneGeometry, ShaderMaterial>;
  private fade = { value: 0 };
  private haze = { value: LOOKS.light.haze.clone() };
  private hazeAmount = { value: 0 };
  private look: Look = LOOKS.dark;
  private bounds: ModelBounds = { height: 1, radius: 0.5 };
  private tier = deviceTier();

  private time = 0;
  private frozenAt: number | null;
  private raf = 0;
  private last = 0;
  private phase: PhaseName | null = null;
  private ended = false;
  private disposed = false;
  /** Past the timeline: the tree is on the page for good and only idles. */
  private settled = false;
  /** Paused from outside, e.g. scrolled out of view. */
  private suspended = false;
  /** Stopped animating because frames were too slow, but the last frame stays. */
  private perfFrozen = false;

  // Frame-time watchdog.
  private frames = 0;
  private slowFrames = 0;
  private degraded = false;

  constructor(
    private container: HTMLElement,
    private options: EngineOptions,
    private callbacks: EngineCallbacks,
  ) {
    this.frozenAt = options.freezeAt;
    // A fresh canvas per engine: dispose() force-loses its context, and React
    // strict mode would otherwise hand the dead canvas to the second mount.
    const canvas = document.createElement("canvas");
    canvas.style.cssText = "display:block;width:100%;height:100%";
    container.appendChild(canvas);
    this.canvas = canvas;
    this.renderer = new WebGLRenderer({
      canvas,
      antialias: true,
      alpha: true,
      premultipliedAlpha: true,
      powerPreference: "high-performance",
    });
    this.renderer.setClearColor(0x000000, 0);
    this.renderer.toneMapping = ACESFilmicToneMapping;
    this.renderer.toneMappingExposure = 1.4;

    this.reveal = new Mesh(
      new PlaneGeometry(2, 2),
      new ShaderMaterial({
        vertexShader: revealVertex,
        fragmentShader: revealFragment,
        uniforms: {
          uOrigin: { value: [0.5, 0.5] },
          uAspect: { value: 1 },
          uRadius: { value: 0 },
          uTime: { value: 0 },
          uRimA: { value: RIM_A },
          uRimB: { value: RIM_B },
          uWash: { value: WASH },
        },
        blending: NoBlending,
        depthTest: false,
        depthWrite: false,
        toneMapped: false,
      }),
    );
    this.reveal.frustumCulled = false;
    this.reveal.renderOrder = -1;
    this.scene.add(this.reveal, this.group);

    this.scene.add(new HemisphereLight(0xdcefff, 0x3a2c1e, 3));
    const sun = new DirectionalLight(0xfff0d8, 4.5);
    sun.position.set(1.5, 2.5, 2);
    this.scene.add(sun);

    canvas.addEventListener("webglcontextlost", this.onContextLost);
    window.addEventListener("resize", this.resize);
    document.addEventListener("visibilitychange", this.onVisibility);
    this.resize();
    void this.load();
  }

  private async load() {
    try {
      await withTimeout(this.build(), this.options.loadTimeoutMs);
    } catch (err) {
      if (!this.disposed) {
        console.warn("[intro] skipped:", err);
        this.end("error");
      }
      return;
    }
    if (this.disposed) return;
    if (this.options.ambient) this.enterSettled();
    this.renderFrame();
    this.callbacks.onReady();
    this.resumeLoop();
  }

  private async build() {
    const buffer = await fetchModel();
    if (this.disposed) return;
    const loader = new GLTFLoader().setMeshoptDecoder(MeshoptDecoder);
    const gltf = await loader.parseAsync(buffer, MODEL_URL.slice(0, MODEL_URL.lastIndexOf("/") + 1));
    if (this.disposed) {
      disposeObject(gltf.scene);
      return;
    }
    const tree = gltf.scene;
    this.bounds = normaliseModel(tree);
    tree.traverse((obj) => {
      const mesh = obj as Mesh;
      if (!mesh.isMesh) return;
      const material = mesh.material as MeshStandardMaterial;
      makeFadeable(material, { fade: this.fade, haze: this.haze, hazeAmount: this.hazeAmount });
    });
    tree.visible = false;
    this.tree = tree;

    const data = sampleModel(tree, this.bounds, this.tier.particles);
    const geometry = new BufferGeometry();
    // Points are positioned in the shader; `position` only exists for three.js bookkeeping.
    geometry.setAttribute("position", new BufferAttribute(data.target, 3));
    geometry.setAttribute("aTarget", new BufferAttribute(data.target, 3));
    geometry.setAttribute("aStart", new BufferAttribute(data.start, 3));
    geometry.setAttribute("aDelay", new BufferAttribute(data.delay, 2));
    geometry.setAttribute("aColor", new BufferAttribute(data.color, 3));
    geometry.setAttribute("aSize", new BufferAttribute(data.size, 1));
    geometry.setAttribute("aSeed", new BufferAttribute(data.seed, 1));

    const material = new ShaderMaterial({
      vertexShader: particlesVertex,
      fragmentShader: particlesFragment,
      uniforms: {
        uTime: { value: 0 },
        uRush: { value: 0 },
        uAssemble: { value: 0 },
        uSolid: { value: 0 },
        uMoteTime: { value: 0 },
        uMoteFraction: { value: MOTE_FRACTION },
        uEdgeX: { value: 1 },
        uSpin: { value: 0 },
        // Fewer particles on small devices, so each one is drawn larger.
        uBaseSize: { value: 0.0065 * Math.sqrt(60000 / data.count) },
        uPixelScale: { value: 1 },
        uCoolColor: { value: new Color("#5eead4").convertSRGBToLinear() },
        uMoteColor: { value: this.look.moteColor },
        uMoteScale: { value: 1 },
        uCover: { value: 0 },
      },
      transparent: true,
      depthWrite: false,
      // Premultiplied "over"; the shader's uCover turns it into pure additive light at 0.
      blending: CustomBlending,
      blendEquation: AddEquation,
      blendSrc: OneFactor,
      blendDst: OneMinusSrcAlphaFactor,
      blendSrcAlpha: OneFactor,
      blendDstAlpha: OneMinusSrcAlphaFactor,
    });
    // The shader never reads `position`, so the driver drops it and warns when three.js pins it to location 0.
    material.index0AttributeName = "aTarget";
    const points = new Points(geometry, material);
    points.frustumCulled = false;
    points.renderOrder = 1;
    this.points = points;

    this.group.add(tree, points);
    this.resize();
    if (this.disposed) return;

    // Compile while the cover is still up, so the first frames do not hitch.
    tree.visible = true;
    // compileAsync() warns when KHR_parallel_shader_compile is missing; compile() is the same work, blocking.
    if (this.renderer.extensions.has("KHR_parallel_shader_compile")) {
      await this.renderer.compileAsync(this.scene, this.camera);
    } else {
      this.renderer.compile(this.scene, this.camera);
    }
    tree.visible = false;
  }

  private tick = (now: number) => {
    this.raf = 0;
    if (this.disposed) return;
    // Clamp the step: after a hitch the intro runs slightly long instead of skipping a phase.
    const dt = Math.min(now - this.last, 50) / 1000;
    this.last = now;
    if (this.frozenAt === null) this.time += dt;
    if (this.options.hold) this.time = Math.min(this.time, TOTAL_DURATION);
    this.watchdog(dt);
    this.renderFrame();

    if (!this.options.hold && !this.settled && this.time >= TOTAL_DURATION) {
      this.settled = true;
      this.callbacks.onEnd("finished");
    }
    if (this.perfFrozen || this.ended || this.suspended) return;
    this.raf = requestAnimationFrame(this.tick);
  };

  private enterSettled() {
    this.time = Math.max(this.time, TOTAL_DURATION);
    this.settled = true;
  }

  /** Jump to the end of the timeline and carry on as the idle tree (used when the intro is skipped). */
  settle() {
    if (this.disposed || this.ended) return;
    this.enterSettled();
    this.frozenAt = null;
    if (this.tree) this.renderFrame();
    this.resumeLoop();
  }

  /** Re-tunes the tree and its motes for the page theme behind them. */
  setTheme(theme: Theme) {
    this.look = LOOKS[theme];
    this.renderer.toneMappingExposure = this.look.exposure;
    this.haze.value.copy(this.look.haze);
    this.hazeAmount.value = this.look.hazeAmount;
    // Without a running loop (still tree, frozen, scrolled away) nothing would redraw it.
    if (this.tree && !this.raf && !this.ended && !this.disposed) this.renderFrame();
  }

  /** Pause or resume rendering without touching any state (e.g. while scrolled out of view). */
  setSuspended(suspended: boolean) {
    if (suspended === this.suspended) return;
    this.suspended = suspended;
    if (suspended) {
      cancelAnimationFrame(this.raf);
      this.raf = 0;
    } else {
      this.resumeLoop();
    }
  }

  private resumeLoop() {
    // A still tree is drawn by renderFrame() on load and resize, never per frame.
    const idle = this.settled && this.options.still;
    if (this.raf || !this.tree || this.ended || this.disposed || this.suspended || this.perfFrozen || idle) return;
    if (document.hidden) return;
    this.last = performance.now();
    this.raf = requestAnimationFrame(this.tick);
  }

  private renderFrame() {
    const t = this.frozenAt ?? this.time;
    const f = frameAt(t);
    const phase = currentPhase(t);
    if (phase !== this.phase) {
      this.phase = phase;
      this.callbacks.onPhase(phase);
    }

    // A slow turntable keeps the tree from looking like a still image.
    const spin = -0.35 + t * 0.09;
    this.group.rotation.y = spin;

    if (this.points) {
      const u = this.points.material.uniforms;
      u.uTime.value = t;
      u.uRush.value = f.rush;
      u.uAssemble.value = f.assemble;
      u.uSolid.value = f.solid;
      u.uMoteTime.value = Math.max(0, t - PHASES.solidify[0]);
      u.uSpin.value = spin;
      u.uMoteColor.value = this.look.moteColor;
      u.uMoteScale.value = this.look.moteScale;
      // Only once the page shows through: over the intro's black the particles always glow.
      u.uCover.value = this.look.moteCover * f.reveal;
    }
    if (this.tree) {
      this.fade.value = f.solid;
      this.tree.visible = f.solid > 0;
    }

    const r = this.reveal.material.uniforms;
    r.uTime.value = t;
    r.uRadius.value = f.reveal * this.revealEnd();
    this.renderer.render(this.scene, this.camera);
  }

  private originUv = new Vector3();

  private revealEnd(): number {
    const [x, y] = this.reveal.material.uniforms.uOrigin.value as number[];
    const aspect = this.camera.aspect;
    let far = 0;
    for (const [cx, cy] of [[0, 0], [1, 0], [0, 1], [1, 1]]) {
      far = Math.max(far, Math.hypot((cx - x) * aspect, cy - y));
    }
    // The noisy front can lag the radius by a third; overshoot so corners clear.
    return far / 0.6 + 0.1;
  }

  private watchdog(dt: number) {
    // Skip the first frames: they include texture uploads and the first draw.
    if (++this.frames < 30 || this.frozenAt !== null) return;
    this.slowFrames = dt > 0.045 ? this.slowFrames + 1 : Math.max(0, this.slowFrames - 1);
    if (this.slowFrames < 20) return;
    this.slowFrames = 0;
    if (!this.degraded && this.points) {
      // Particles are stored in random order, so the first half is an even subset.
      this.degraded = true;
      this.points.geometry.setDrawRange(0, Math.floor(this.points.geometry.attributes.aTarget.count / 2));
      this.renderer.setPixelRatio(1);
    } else if (this.settled) {
      // Struggling as a background: stop animating rather than tear the tree down.
      this.perfFrozen = true;
    } else {
      this.end("slow");
    }
  }

  private resize = () => {
    const width = this.container.clientWidth || window.innerWidth;
    const height = this.container.clientHeight || window.innerHeight;
    const aspect = width / height;
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, this.degraded ? 1 : this.tier.maxPixelRatio));
    this.renderer.setSize(width, height, false);
    this.camera.aspect = aspect;

    // Fit the tree's height and its full width (with some margin) on any aspect ratio.
    const tan = Math.tan((FOV * Math.PI) / 360);
    const halfHeight = Math.max(0.5 / 0.8, (this.bounds.radius + 0.04) / 0.8 / aspect);
    const distance = halfHeight / tan + this.bounds.radius;
    this.camera.position.set(0, 0.52, distance);
    this.camera.lookAt(0, 0.5, 0);
    this.camera.updateProjectionMatrix();
    this.camera.updateMatrixWorld();

    if (this.points) {
      const u = this.points.material.uniforms;
      // Half the visible width on the trunk's plane: particles start just past it.
      u.uEdgeX.value = distance * tan * aspect;
      u.uPixelScale.value = this.camera.projectionMatrix.elements[5] * height * 0.5 * this.renderer.getPixelRatio();
    }
    // The wavefront starts from the middle of the canopy, in screen uv.
    this.originUv.set(0, 0.45, 0).project(this.camera);
    const r = this.reveal.material.uniforms;
    r.uOrigin.value = [this.originUv.x * 0.5 + 0.5, this.originUv.y * 0.5 + 0.5];
    r.uAspect.value = aspect;
    if (this.tree) this.renderFrame();
  };

  private onVisibility = () => {
    if (document.hidden) {
      cancelAnimationFrame(this.raf);
      this.raf = 0;
    } else {
      this.resumeLoop();
    }
  };

  private onContextLost = () => this.end("error");

  private end(reason: EndReason) {
    if (this.ended) return;
    this.ended = true;
    cancelAnimationFrame(this.raf);
    this.callbacks.onEnd(reason);
  }

  get currentTime(): number {
    return this.frozenAt ?? this.time;
  }

  seek(t: number) {
    this.frozenAt = t;
    if (this.tree) this.renderFrame();
  }

  play() {
    if (this.frozenAt !== null) this.time = this.frozenAt;
    this.frozenAt = null;
  }

  pause() {
    this.frozenAt = this.time;
  }

  dispose() {
    this.disposed = true;
    cancelAnimationFrame(this.raf);
    this.canvas.removeEventListener("webglcontextlost", this.onContextLost);
    window.removeEventListener("resize", this.resize);
    document.removeEventListener("visibilitychange", this.onVisibility);
    disposeObject(this.scene);
    this.renderer.dispose();
    this.renderer.forceContextLoss();
    this.canvas.remove();
  }
}

function disposeObject(root: { traverse(cb: (obj: unknown) => void): void }) {
  const textures = new Set<Texture>();
  root.traverse((obj) => {
    const mesh = obj as Mesh;
    mesh.geometry?.dispose();
    const materials = (Array.isArray(mesh.material) ? mesh.material : [mesh.material]) as (Material | undefined)[];
    for (const material of materials) {
      if (!material) continue;
      for (const value of Object.values(material)) {
        if (value instanceof Texture) textures.add(value);
      }
      material.dispose();
    }
  });
  for (const texture of textures) {
    const image = texture.image as { close?: () => void } | undefined;
    image?.close?.();
    texture.dispose();
  }
}
