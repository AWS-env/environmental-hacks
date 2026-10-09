import { Box3, Color, Mesh, Object3D, SRGBColorSpace, Texture, Vector2, Vector3 } from "three";
import type { MeshStandardMaterial } from "three";
import { MeshSurfaceSampler } from "three/examples/jsm/math/MeshSurfaceSampler.js";

// Turns the tree model into particle targets. Runs once on load, never per frame.

export type PartKind = "wood" | "leaves" | "vines";

/** Fraction of particles that stay alive as floating motes after the tree solidifies. */
export const MOTE_FRACTION = 0.035;

// Material names from the Tree GN model. Anything unknown is treated by its alpha mode.
const KIND_BY_MATERIAL: Record<string, PartKind> = {
  BarkB: "wood",
  CortexB: "wood",
  PruneB: "wood",
  ClusterB: "leaves",
  ClusterB2: "leaves",
  VinesB: "vines",
};

// Relative particle density per part, so the trunk still reads under a dense canopy.
const DENSITY: Record<PartKind, number> = { wood: 1.6, leaves: 1, vines: 0.8 };

export interface ModelBounds {
  height: number;
  /** Largest horizontal distance from the trunk axis. */
  radius: number;
}

export interface ParticleData {
  count: number;
  target: Float32Array;
  start: Float32Array;
  /** x: growth order (0 roots .. 1 canopy), y: rush-in stagger. */
  delay: Float32Array;
  color: Float32Array;
  size: Float32Array;
  seed: Float32Array;
}

/**
 * Centres the model on the trunk axis with its base at y = 0 and scales it to
 * a height of 1, so camera framing does not depend on the source file's units.
 */
export function normaliseModel(model: Object3D): ModelBounds {
  model.updateMatrixWorld(true);
  const box = new Box3().setFromObject(model);
  const size = box.getSize(new Vector3());
  const centre = box.getCenter(new Vector3());
  const scale = 1 / size.y;
  model.scale.multiplyScalar(scale);
  model.position.set(-centre.x * scale, -box.min.y * scale, -centre.z * scale);
  model.updateMatrixWorld(true);
  return { height: 1, radius: (Math.max(size.x, size.z) * scale) / 2 };
}

interface Pixels {
  data: Uint8ClampedArray;
  width: number;
  height: number;
}

// Reads a texture back to the CPU so samples can take its colour and alpha.
// Downscaled: 256px is plenty for picking colours and rejecting transparent texels.
function readPixels(texture: Texture | null): Pixels | null {
  const image = texture?.image as (CanvasImageSource & { width: number; height: number }) | undefined;
  if (!image?.width) return null;
  const width = Math.min(256, image.width);
  const height = Math.min(256, image.height);
  const canvas = document.createElement("canvas");
  canvas.width = width;
  canvas.height = height;
  const ctx = canvas.getContext("2d", { willReadFrequently: true });
  if (!ctx) return null;
  ctx.drawImage(image, 0, 0, width, height);
  return { data: ctx.getImageData(0, 0, width, height).data, width, height };
}

interface Part {
  mesh: Mesh;
  kind: PartKind;
  sampler: MeshSurfaceSampler;
  pixels: Pixels | null;
  alphaCutoff: number;
  tint: Color;
}

function collectParts(model: Object3D): Part[] {
  const parts: Part[] = [];
  model.traverse((obj) => {
    const mesh = obj as Mesh;
    if (!mesh.isMesh) return;
    const material = mesh.material as MeshStandardMaterial;
    const kind = KIND_BY_MATERIAL[material.name] ?? (material.alphaTest > 0 ? "leaves" : "wood");
    parts.push({
      mesh,
      kind,
      sampler: new MeshSurfaceSampler(mesh).build(),
      pixels: readPixels(material.map),
      alphaCutoff: material.alphaTest > 0 ? material.alphaTest : 0.5,
      tint: material.color.clone(),
    });
  });
  return parts;
}

export function sampleModel(model: Object3D, bounds: ModelBounds, count: number): ParticleData {
  const parts = collectParts(model);
  // Pick parts by world-space area times density, so particles spread evenly.
  const weights = parts.map((p) => {
    const dist = p.sampler.distribution!;
    const scale = p.mesh.matrixWorld.getMaxScaleOnAxis();
    return dist[dist.length - 1] * scale * scale * DENSITY[p.kind];
  });
  const totalWeight = weights.reduce((a, b) => a + b, 0);

  const target = new Float32Array(count * 3);
  const start = new Float32Array(count * 3);
  const delay = new Float32Array(count * 2);
  const color = new Float32Array(count * 3);
  const size = new Float32Array(count);
  const seed = new Float32Array(count);
  const kinds: PartKind[] = new Array(count);

  const pos = new Vector3();
  const normal = new Vector3();
  const vertexColor = new Color();
  const uv = new Vector2();
  const texel = new Color();

  let n = 0;
  let attempts = 0;
  const maxAttempts = count * 40;
  while (n < count && attempts < maxAttempts) {
    attempts++;
    let r = Math.random() * totalWeight;
    let i = 0;
    while (i < parts.length - 1 && r > weights[i]) r -= weights[i++];
    const part = parts[i];
    vertexColor.setRGB(1, 1, 1);
    part.sampler.sample(pos, normal, vertexColor, uv);

    texel.setRGB(1, 1, 1);
    if (part.pixels) {
      const { data, width, height } = part.pixels;
      // glTF UVs have their origin at the top-left, matching canvas rows.
      const x = Math.floor((uv.x - Math.floor(uv.x)) * width);
      const y = Math.floor((uv.y - Math.floor(uv.y)) * height);
      const o = (y * width + x) * 4;
      // Skip transparent parts of the leaf and vine cards.
      if (data[o + 3] / 255 < part.alphaCutoff) continue;
      texel.setRGB(data[o] / 255, data[o + 1] / 255, data[o + 2] / 255, SRGBColorSpace);
    }

    pos.applyMatrix4(part.mesh.matrixWorld);
    normal.transformDirection(part.mesh.matrixWorld);
    // A small push along the normal avoids z-fighting with the mesh in the cross-fade.
    pos.addScaledVector(normal, 0.0025);
    pos.toArray(target, n * 3);

    texel.multiply(vertexColor).multiply(part.tint);
    texel.toArray(color, n * 3);

    // Start just off the left or right edge; x is in units of the visible half-width.
    // Most particles enter from the side their target is on, a few cross over.
    const fromLeft = (pos.x < 0) !== Math.random() < 0.2;
    start[n * 3] = (fromLeft ? -1 : 1) * (1.08 + Math.random() * 0.4);
    start[n * 3 + 1] = -0.15 + Math.random() * 1.3;
    start[n * 3 + 2] = (Math.random() - 0.5) * 1.2;

    size[n] = (part.kind === "wood" ? 0.8 : 1) * (0.6 + Math.random() * Math.random() * 1.2);
    seed[n] = Math.random();
    kinds[n] = part.kind;
    n++;
  }

  // Growth order: roots and trunk first, then branches, then the canopy.
  // Height and distance from the trunk axis carry most of it; jitter keeps it
  // from reading as a hard scan line.
  let minG = Infinity;
  let maxG = -Infinity;
  for (let k = 0; k < n; k++) {
    const x = target[k * 3];
    const y = target[k * 3 + 1] / bounds.height;
    const z = target[k * 3 + 2];
    const radial = Math.min(1, Math.hypot(x, z) / bounds.radius);
    let g: number;
    if (kinds[k] === "wood") g = 0.65 * y + 0.35 * radial;
    else if (kinds[k] === "leaves") g = 0.3 + 0.45 * y + 0.35 * radial;
    else g = 0.55 + 0.45 * Math.random(); // vines hang, so height says little
    g += (Math.random() - 0.5) * 0.08;
    delay[k * 2] = g;
    delay[k * 2 + 1] = Math.random();
    minG = Math.min(minG, g);
    maxG = Math.max(maxG, g);
  }
  for (let k = 0; k < n; k++) delay[k * 2] = (delay[k * 2] - minG) / (maxG - minG || 1);

  return {
    count: n,
    target: target.subarray(0, n * 3),
    start: start.subarray(0, n * 3),
    delay: delay.subarray(0, n * 2),
    color: color.subarray(0, n * 3),
    size: size.subarray(0, n),
    seed: seed.subarray(0, n),
  };
}
