// Full-screen backdrop drawn before the tree. Outside the wavefront it is
// opaque (black at night, pale sky by day); inside it is transparent, so the real
// page shows through. Night colours are premultiplied and may exceed alpha: that
// adds light over the page. Day has no light to add, so the rim is painted ink.

export const revealVertex = /* glsl */ `
varying vec2 vUv;

void main() {
  vUv = uv;
  gl_Position = vec4(position.xy, 0.0, 1.0);
}
`;

export const revealFragment = /* glsl */ `
uniform vec2 uOrigin;   // tree position in 0..1 screen uv
uniform float uAspect;
uniform float uRadius;  // 0 before the reveal, past the farthest corner at the end
uniform float uTime;
uniform vec3 uRimA;
uniform vec3 uRimB;
uniform vec3 uWash;
uniform vec3 uBackdrop; // display colour of the opaque side
uniform float uPaint;   // 0: additive glow (night), 1: painted rim (day)

varying vec2 vUv;

float hash(vec2 p) {
  p = fract(p * vec2(123.34, 456.21));
  p += dot(p, p + 45.32);
  return fract(p.x * p.y);
}

float noise(vec2 p) {
  vec2 i = floor(p);
  vec2 f = fract(p);
  vec2 u = f * f * (3.0 - 2.0 * f);
  return mix(
    mix(hash(i), hash(i + vec2(1.0, 0.0)), u.x),
    mix(hash(i + vec2(0.0, 1.0)), hash(i + vec2(1.0, 1.0)), u.x),
    u.y
  );
}

float fbm(vec2 p) {
  float v = 0.0;
  float a = 0.5;
  for (int i = 0; i < 5; i++) {
    v += a * noise(p);
    p = p * 2.03 + vec2(17.0, 9.0);
    a *= 0.5;
  }
  return v;
}

void main() {
  vec2 p = (vUv - uOrigin) * vec2(uAspect, 1.0);
  float d = length(p);
  vec2 dir = p / max(d, 1e-4);

  float started = smoothstep(0.0, 0.04, uRadius);
  // Noise on the direction alone grows radial fingers, like roots or mycelium;
  // noise on position breaks the fingers into finer tendrils.
  float tendril = fbm(dir * 2.4 + vec2(uTime * 0.04, 3.0));
  float detail = fbm(p * 7.0 - uTime * 0.12);
  float front = uRadius * (0.65 + 0.7 * tendril) + 0.07 * (detail - 0.5) * started - 0.02 * (1.0 - started);
  float edge = d - front; // negative inside the wavefront

  float outside = smoothstep(-0.004, 0.004, edge);
  float rim = exp(-abs(edge) / 0.005) * 0.9 + exp(-abs(edge) / 0.045) * 0.3;
  // A tint that lingers just behind the front, then clears to the page.
  float wash = (1.0 - outside) * exp(edge * 7.0) * 0.4;

  vec3 rimColor = mix(uRimA, uRimB, smoothstep(0.35, 0.65, detail));
  if (uPaint > 0.5) {
    float rimW = rim * started * 0.8;
    float washW = wash * started * 0.6;
    float paint = clamp(rimW + washW, 0.0, 1.0);
    vec3 ink = (rimColor * rimW + uWash * washW) / max(rimW + washW, 1e-4);
    // Premultiplied: ink over the backdrop.
    gl_FragColor = vec4(ink * paint + uBackdrop * outside * (1.0 - paint), paint + outside * (1.0 - paint));
    return;
  }
  vec3 glow = (rimColor * rim + uWash * wash) * started;
  gl_FragColor = vec4(glow, outside);
}
`;
