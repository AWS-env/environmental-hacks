// Particle morph. The CPU only updates a handful of uniforms per frame; every
// particle's path is computed here from its attributes and the phase progress.

export const particlesVertex = /* glsl */ `
attribute vec3 aTarget;
attribute vec3 aStart;
attribute vec2 aDelay; // x: growth order, y: rush-in stagger
attribute vec3 aColor;
attribute float aSize;
attribute float aSeed;

uniform float uTime;
uniform float uRush;
uniform float uAssemble;
uniform float uSolid;
uniform float uMoteTime;
uniform float uMoteFraction;
uniform float uEdgeX;
uniform float uSpin;
uniform float uBaseSize;
uniform float uPixelScale;
uniform vec3 uCoolColor;
uniform vec3 uMoteColor;
uniform float uMoteScale;

varying vec3 vColor;
varying float vAlpha;

const float PI = 3.141592653589793;

vec3 rotateY(vec3 p, float a) {
  float c = cos(a);
  float s = sin(a);
  return vec3(c * p.x + s * p.z, p.y, -s * p.x + c * p.z);
}

// Layered trig flow field. Not true curl noise, but it bends the paths into
// swirls at a fraction of the cost, which matters at 60k vertices on mobile.
vec3 flow(vec3 p, float t) {
  float s = aSeed * 6.2831;
  return vec3(
    sin(p.y * 4.1 + t * 1.3 + s) + 0.5 * sin(p.z * 7.3 - t * 0.9),
    sin(p.z * 3.7 + t * 1.1 + s * 0.5) + 0.5 * sin(p.x * 6.1 + t * 1.7),
    sin(p.x * 4.3 - t * 1.2 + s * 0.8) + 0.5 * sin(p.y * 5.9 - t * 1.4)
  );
}

float easeOutCubic(float x) { return 1.0 - pow(1.0 - x, 3.0); }
float easeInOutCubic(float x) {
  return x < 0.5 ? 4.0 * x * x * x : 1.0 - pow(-2.0 * x + 2.0, 3.0) / 2.0;
}

void main() {
  // Staging cloud: a wider, orbiting copy of the tree that the rush flows into.
  float radius = length(aTarget.xz);
  float angle = radius > 1e-4 ? atan(aTarget.z, aTarget.x) : aSeed * 2.0 * PI;
  float orbit = angle + uTime * (0.55 + aSeed * 0.5);
  float cloudR = radius * 1.45 + 0.1 + 0.14 * aSeed;
  vec3 cloud = vec3(
    cos(orbit) * cloudR,
    aTarget.y * 0.9 + 0.06 + 0.05 * sin(uTime * 1.5 + aSeed * 20.0),
    sin(orbit) * cloudR
  );

  // Rush in from just off the screen edge. Starts are screen-fixed, so undo
  // the group's slow spin.
  vec3 start = rotateY(vec3(aStart.x * uEdgeX, aStart.y, aStart.z), -uSpin);
  float rushT = clamp((uRush - aDelay.y * 0.45) / 0.55, 0.0, 1.0);
  vec3 p = mix(start, cloud, easeOutCubic(rushT));
  p += flow(p * 1.3, uTime) * 0.07 * sin(PI * rushT);

  // Assemble in growth order: roots and trunk first, canopy last.
  float buildT = clamp((uAssemble - aDelay.x * 0.7) / 0.3, 0.0, 1.0);
  float built = easeInOutCubic(buildT);
  p = mix(p, aTarget, built);
  p += flow(aTarget * 2.0, uTime) * 0.03 * sin(PI * buildT);

  // Idle shimmer keeps the settled tree alive.
  p += flow(aTarget * 9.0, uTime * 0.7) * 0.0012 * built;

  float alpha = smoothstep(0.0, 0.25, rushT);
  float size = aSize * uBaseSize * (1.0 + 0.5 * (1.0 - built) * rushT);
  size *= 1.0 + 0.2 * sin(uTime * 3.0 + aSeed * 50.0) * built;

  // Colour starts as a cool glow and takes on the tree's own colour as it settles.
  vec3 treeColor = aColor * 2.6 + 0.02;
  vec3 color = mix(uCoolColor, treeColor, smoothstep(0.2, 1.0, built) * 0.75 + 0.25 * uSolid);

  bool mote = aSeed < uMoteFraction;
  if (mote) {
    // Motes lift off the tree and drift once it turns solid. The tree now stays
    // on the page, so each mote loops on its own period (always longer than the
    // intro's ~2.3s of drift, which therefore plays unchanged): it fades out at
    // the top and fades back in on the canopy instead of rising forever.
    float period = 4.0 + 3.0 * fract(aSeed * 37.0);
    float cycle = floor(uMoteTime / period);
    float age = uMoteTime - cycle * period;
    float loopFade = (1.0 - smoothstep(period - 1.0, period, age))
      * (cycle > 0.0 ? smoothstep(0.0, 0.6, age) : 1.0);

    float lift = smoothstep(0.0, 1.0, uSolid);
    vec2 outward = radius > 1e-4 ? aTarget.xz / radius : vec2(0.0);
    p += lift * (flow(aTarget * 1.7, uTime * 0.35) * 0.04
      + vec3(outward.x, 0.0, outward.y) * 0.05
      + vec3(0.0, 0.035 * age, 0.0));
    color = mix(color, uMoteColor, lift);
    size *= (1.0 + 0.8 * lift) * mix(1.0, uMoteScale, lift);
    alpha *= (1.0 - 0.3 * lift * (0.5 + 0.5 * sin(uTime * 2.3 + aSeed * 90.0))) * loopFade;
  } else {
    // Everything else condenses into the mesh.
    alpha *= 1.0 - uSolid;
    size *= 1.0 - 0.6 * uSolid;
  }

  vec4 mvPosition = modelViewMatrix * vec4(p, 1.0);
  gl_Position = projectionMatrix * mvPosition;
  gl_PointSize = clamp(size * uPixelScale / -mvPosition.z, 0.0, 48.0);
  vColor = color;
  vAlpha = alpha;
}
`;

export const particlesFragment = /* glsl */ `
uniform float uCover;

varying vec3 vColor;
varying float vAlpha;

void main() {
  float d = length(gl_PointCoord - 0.5);
  if (d > 0.5 || vAlpha <= 0.0) discard;
  // Soft sprite with a brighter core.
  float a = smoothstep(0.5, 0.0, d);
  a *= a * vAlpha;
  vec4 color = linearToOutputTexel(vec4(vColor, 1.0));
  // Premultiplied, with uCover deciding how much the sprite hides what is behind it:
  // 0 is pure additive light (rgb adds, alpha untouched), which glows over black
  // and the night page; 1 is ordinary paint, which keeps gold motes gold on the
  // day sky, where added light would wash out to white.
  gl_FragColor = vec4(color.rgb * a, a * uCover);
}
`;
