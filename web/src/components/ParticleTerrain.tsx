"use client";

import { useEffect, useRef } from "react";
import * as THREE from "three";

const vertexShader = `
  uniform float uTime, uSeed, uLayer, uPixelRatio;
  attribute float aRandom;
  varying float vAlpha;
  float hash(vec2 p) {
    return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453);
  }
  float noise(vec2 p) {
    vec2 i = floor(p), f = fract(p);
    vec2 s = f * f * (3.0 - 2.0 * f);
    return mix(mix(hash(i), hash(i + vec2(1.0, 0.0)), s.x),
               mix(hash(i + vec2(0.0, 1.0)), hash(i + vec2(1.0)), s.x), s.y);
  }
  float field(vec2 p) {
    return noise(p) * 0.57 + noise(p * 2.03 + 17.3) * 0.28
         + noise(p * 4.11 + 31.7) * 0.15;
  }
  void main() {
    vec3 p = position;
    float t = uTime * 0.19;
    vec2 q = vec2(p.x * 0.0045, p.y * 0.007) + uSeed + uLayer * 4.7;
    float drift = field(q + vec2(t * 0.31, -t * 0.22));
    float folds = sin(p.x * 0.012 + p.y * 0.019 + drift * 5.0 - t);
    float detail = sin(p.x * 0.024 - p.y * 0.016 + t * 0.67);
    float ribbon = sin(p.x * 0.004 + t * 0.43 + uLayer) * 66.0;
    float edge = 1.0 - smoothstep(105.0, 225.0, abs(p.y));
    p.z = (drift - 0.5) * 220.0 + folds * 48.0 + detail * 12.0 - uLayer * 65.0;
    p.y += ribbon + folds * 27.0 + (drift - 0.5) * 85.0 - p.x * 0.36 - uLayer * 105.0;
    p.x += sin(q.y * 2.0 + t * 0.71) * 10.0;
    p.y += sin(t * 0.8 + aRandom * 6.283) * 2.2;
    vec4 viewPosition = modelViewMatrix * vec4(p, 1.0);
    gl_Position = projectionMatrix * viewPosition;
    gl_PointSize = clamp((1.65 + aRandom * 0.95) * uPixelRatio
                        * (1000.0 / -viewPosition.z), 1.35, 4.8);
    float crest = 0.48 + 0.52 * smoothstep(-0.8, 0.9, folds);
    vAlpha = edge * crest * (0.48 + aRandom * 0.34) * (1.0 - uLayer * 0.25);
  }
`;
const fragmentShader = `
  varying float vAlpha;
  void main() {
    float radius = length(gl_PointCoord - 0.5);
    float feather = max(fwidth(radius), 0.08);
    float dotAlpha = 1.0 - smoothstep(0.5 - feather, 0.5, radius);
    if (dotAlpha < 0.01) discard;
    gl_FragColor = vec4(vec3(0.91, 0.94, 0.96), dotAlpha * vAlpha);
  }
`;

export default function ParticleTerrain() {
  const mountRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const container = mountRef.current;
    if (!container) return;
    let renderer: THREE.WebGLRenderer;
    try {
      renderer = new THREE.WebGLRenderer({ alpha: true, antialias: false, depth: false, powerPreference: "low-power" });
    } catch {
      // Keep the CSS backdrop if WebGL is unavailable.
      return;
    }
    const scene = new THREE.Scene();
    const camera = new THREE.PerspectiveCamera(48, 1, 1, 2200);
    camera.position.set(0, 0, 1000);
    const smallScreen = window.innerWidth < 768;
    const columns = smallScreen ? 170 : 310, rows = smallScreen ? 65 : 130;
    const positions = new Float32Array(columns * rows * 3);
    const randomness = new Float32Array(columns * rows);
    for (let col = 0; col < columns; col++) {
      for (let row = 0; row < rows; row++) {
        const index = col * rows + row;
        positions[index * 3] = (col / (columns - 1) - 0.5) * 1600 + (Math.random() - 0.5) * 1.3;
        positions[index * 3 + 1] = (row / (rows - 1) - 0.5) * 450 + (Math.random() - 0.5) * 1.3;
        randomness[index] = Math.random();
      }
    }
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute("position", new THREE.BufferAttribute(positions, 3));
    geometry.setAttribute("aRandom", new THREE.BufferAttribute(randomness, 1));
    const seed = Math.random() * 100;
    const materials: THREE.ShaderMaterial[] = [];
    for (let layer = 0; layer < (smallScreen ? 1 : 2); layer++) {
      const material = new THREE.ShaderMaterial({
        vertexShader, fragmentShader,
        uniforms: { uTime: { value: 0 }, uSeed: { value: seed }, uLayer: { value: layer }, uPixelRatio: { value: 1 } },
        transparent: true, depthWrite: false, depthTest: false,
      });
      materials.push(material);
      const points = new THREE.Points(geometry, material);
      // Shader displacement can extend beyond the original bounds.
      points.frustumCulled = false;
      scene.add(points);
    }
    const dustGeometry = new THREE.BufferGeometry();
    const dustPositions = new Float32Array((smallScreen ? 160 : 450) * 3);
    for (let i = 0; i < dustPositions.length; i += 3) {
      dustPositions[i] = (Math.random() - 0.5) * 1800;
      dustPositions[i + 1] = (Math.random() - 0.5) * 1000;
      dustPositions[i + 2] = (Math.random() - 0.5) * 300;
    }
    dustGeometry.setAttribute("position", new THREE.BufferAttribute(dustPositions, 3));
    const dustMaterial = new THREE.PointsMaterial({ color: 0xc2cbd0, size: 0.85, transparent: true, opacity: 0.22, depthWrite: false });
    const dust = new THREE.Points(dustGeometry, dustMaterial);
    scene.add(dust);
    const canvas = renderer.domElement;
    canvas.style.display = "block";
    container.appendChild(canvas);
    const motion = window.matchMedia("(prefers-reduced-motion: reduce)");
    let frameId = 0, lastFrame = 0, elapsed = 0, contextLost = false;
    let pointerX = 0, pointerY = 0;
    function render() {
      for (const material of materials) material.uniforms.uTime.value = elapsed;
      if (!motion.matches) {
        camera.position.x += (pointerX - camera.position.x) * 0.035;
        camera.position.y += (pointerY - camera.position.y) * 0.035;
        dust.rotation.z = Math.sin(elapsed * 0.04) * 0.025;
      }
      camera.lookAt(0, 0, 0);
      renderer.render(scene, camera);
    }
    function animate(now: number) {
      if (document.hidden || motion.matches || contextLost) { frameId = 0; return; }
      frameId = requestAnimationFrame(animate);
      if (now - lastFrame < 1000 / 30) return;
      elapsed += Math.min((now - lastFrame) / 1000, 0.1);
      lastFrame = now;
      render();
    }
    function resume() {
      cancelAnimationFrame(frameId);
      frameId = 0;
      if (document.hidden || contextLost) return;
      render();
      if (!motion.matches) { lastFrame = performance.now(); frameId = requestAnimationFrame(animate); }
    }
    function resize() {
      const width = container!.clientWidth, height = Math.max(container!.clientHeight, 1);
      const pixelRatio = Math.min(window.devicePixelRatio || 1, 1.5);
      renderer.setPixelRatio(pixelRatio);
      renderer.setSize(width, height);
      camera.aspect = width / height;
      camera.position.z = width < 768 ? 1250 : 1000;
      camera.updateProjectionMatrix();
      for (const material of materials) material.uniforms.uPixelRatio.value = pixelRatio;
      if (!document.hidden && !contextLost) render();
    }
    function pointerMove(event: PointerEvent) {
      if (motion.matches || event.pointerType !== "mouse") return;
      pointerX = (event.clientX / window.innerWidth - 0.5) * 22;
      pointerY = -(event.clientY / window.innerHeight - 0.5) * 16;
    }
    function onContextLost(event: Event) {
      event.preventDefault(); contextLost = true;
      cancelAnimationFrame(frameId); frameId = 0;
    }
    function onContextRestored() { contextLost = false; resize(); resume(); }
    window.addEventListener("resize", resize);
    window.addEventListener("pointermove", pointerMove, { passive: true });
    document.addEventListener("visibilitychange", resume);
    motion.addEventListener("change", resume);
    canvas.addEventListener("webglcontextlost", onContextLost);
    canvas.addEventListener("webglcontextrestored", onContextRestored);
    resize(); resume();
    return () => {
      cancelAnimationFrame(frameId);
      window.removeEventListener("resize", resize);
      window.removeEventListener("pointermove", pointerMove);
      document.removeEventListener("visibilitychange", resume);
      motion.removeEventListener("change", resume);
      canvas.removeEventListener("webglcontextlost", onContextLost);
      canvas.removeEventListener("webglcontextrestored", onContextRestored);
      geometry.dispose(); materials.forEach((material) => material.dispose());
      dustGeometry.dispose(); dustMaterial.dispose(); renderer.dispose(); canvas.remove();
    };
  }, []);
  return (
    <div aria-hidden="true" className="pointer-events-none fixed inset-0 z-0 overflow-hidden bg-[#0a0d0e]">
      <div className="absolute inset-0 opacity-30" style={{ backgroundImage: "radial-gradient(ellipse at 20% 35%, #354044 0%, transparent 55%), radial-gradient(ellipse at 90% 85%, #30383b 0%, transparent 50%)" }} />
      <div ref={mountRef} className="absolute inset-0 opacity-90" />
      <div className="absolute inset-0" style={{ background: "radial-gradient(ellipse at 52% 30%, rgba(10,13,14,.64) 0%, transparent 65%), linear-gradient(to bottom, rgba(10,13,14,.25), transparent 60%, rgba(10,13,14,.65))" }} />
    </div>
  );
}
