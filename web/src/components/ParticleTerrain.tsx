"use client";

import { useEffect, useRef } from "react";
import * as THREE from "three";
import { pipelineLayout, PIPELINE_NODES, PIPELINE_STEPS, MORPH_SECONDS, STEP_SECONDS, nodeIndex } from "./pipelineModel";

const vertexShader = `
  uniform float uTime, uSeed, uLayer, uPixelRatio;
  attribute float aRandom;
  attribute float aNode, aRole;
  attribute vec3 aBox;
  uniform float uMorph, uSize, uPhase, uMotion, uActive, uCurve;
  uniform vec2 uViewport, uFrom, uTo;
  uniform vec2 uCenters[14];
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
  float fluidEase(float value) {
    float t = clamp(value, 0.0, 1.0);
    return t*t*t*(t*(t*6.0-15.0)+10.0);
  }
  vec2 flowPath(float f) {
    vec2 c1 = vec2(uFrom.x, (uFrom.y+uTo.y)*0.5);
    vec2 c2 = vec2(uTo.x, (uFrom.y+uTo.y)*0.5);
    vec2 curved = pow(1.0-f,3.0)*uFrom + 3.0*pow(1.0-f,2.0)*f*c1
                + 3.0*(1.0-f)*f*f*c2 + f*f*f*uTo;
    return mix(mix(uFrom,uTo,f),curved,uCurve);
  }
  void main() {
    if (uMorph < 0.999) {
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
    } else {
      gl_Position = vec4(0.0, 0.0, 0.0, 1.0);
      gl_PointSize = 1.0;
      vAlpha = 0.0;
    }
    vec2 center = uCenters[int(aNode)];
    vec3 box = aBox;
    float graphAlpha = 0.0;
    float graphSize = aRole < 0.5 ? 1.65 : 1.8;
    if (aRole < 0.5) {
      graphAlpha = abs(aNode - uActive) < 0.5 ? 1.0 : 0.7;
    } else if (aRole < 1.5) {
      // A bright leading core pulls the remaining particles along the same path.
      // Followers start later, fan out softly, and all settle before the next stage.
      float lag = aRandom < 0.22 ? 0.0 : pow((aRandom-0.22)/0.78,1.2)*0.62;
      float f = fluidEase((uPhase-2.1-lag)/(1.3-lag))*uMotion;
      float collapse = fluidEase((uPhase-1.4-lag*0.2)/0.7)*uMotion;
      float refill = fluidEase((uPhase-3.4-lag*0.3)/(0.8-lag*0.3))*uMotion;
      float scale = mix(mix(1.0,0.035,collapse),1.0,refill);
      center = flowPath(f);
      vec2 direction = flowPath(min(f+0.01,1.0))-flowPath(max(f-0.01,0.0));
      vec2 normal = vec2(-direction.y,direction.x)/max(length(direction),0.001);
      float moving = sin(f*3.14159265);
      float fan = moving*lag*8.0;
      center += normal*(aBox.y*fan + sin(uTime*3.0+aRandom*18.0)*fan*0.24);
      box *= scale;
      box += vec3(sin(uTime*0.9+aRandom*6.28)*0.012*scale);
      graphAlpha = mix(0.48, mix(0.38,0.09,lag/0.62),moving);
      graphSize = mix(1.8, aRandom < 0.22 ? 2.6 : 1.35,moving);
    }
    vec2 offset = vec2(box.x + box.z*0.42, -box.y - box.z*0.35)*uSize;
    vec2 graph = (center + offset)/uViewport*2.0-1.0;
    graph.y = -graph.y;
    float gather = fluidEase(clamp((uMorph-aRandom*0.12)/(1.0-aRandom*0.12),0.0,1.0));
    vec4 terrain = vec4(gl_Position.xyz/gl_Position.w,1.0);
    vec2 arc = vec2(sin(aRandom*6.283),cos(aRandom*6.283))*sin(gather*3.14159265)*0.055;
    gl_Position = mix(terrain,vec4(graph,0.0,1.0),gather);
    gl_Position.xy += arc*uMotion;
    gl_PointSize = mix(gl_PointSize,graphSize*uPixelRatio,gather);
    vAlpha = mix(vAlpha,graphAlpha*(1.0-uLayer),gather);
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

export default function ParticleTerrain({ analyzing = false, onStageChange }: { analyzing?: boolean; onStageChange?: (step: number) => void }) {
  const mountRef = useRef<HTMLDivElement>(null);
  const mode = useRef({ analyzing, onStageChange });
  const refresh = useRef<() => void>(() => {});
  useEffect(() => { mode.current = { analyzing, onStageChange }; refresh.current(); }, [analyzing, onStageChange]);
  useEffect(() => {
    const container = mountRef.current;
    if (!container) return;
    let renderer: THREE.WebGLRenderer;
    try {
      renderer = new THREE.WebGLRenderer({ alpha: true, antialias: false, depth: false, powerPreference: "low-power" });
    } catch {
      document.documentElement.dataset.particleRenderer = "fallback";
      let step = 0;
      const timer = setInterval(() => {
        if (mode.current.analyzing && !document.hidden) mode.current.onStageChange?.(step++ % PIPELINE_STEPS.length);
        else step = 0;
      }, STEP_SECONDS * 1000);
      return () => { clearInterval(timer); delete document.documentElement.dataset.particleRenderer; };
    }
    document.documentElement.dataset.particleRenderer = "webgl";
    const scene = new THREE.Scene();
    const camera = new THREE.PerspectiveCamera(48, 1, 1, 2200);
    camera.position.set(0, 0, 1000);
    const smallScreen = window.innerWidth < 768;
    const columns = smallScreen ? 170 : 310, rows = smallScreen ? 65 : 130;
    const positions = new Float32Array(columns * rows * 3);
    const randomness = new Float32Array(columns * rows);
    const boxes = new Float32Array(columns * rows * 3);
    const nodes = new Float32Array(columns * rows);
    const roles = new Float32Array(columns * rows);
    for (let col = 0; col < columns; col++) {
      for (let row = 0; row < rows; row++) {
        const index = col * rows + row;
        positions[index * 3] = (col / (columns - 1) - 0.5) * 1600 + (Math.random() - 0.5) * 1.3;
        positions[index * 3 + 1] = (row / (rows - 1) - 0.5) * 450 + (Math.random() - 0.5) * 1.3;
        randomness[index] = Math.random();
        nodes[index] = index % PIPELINE_NODES.length;
        roles[index] = index % 10 < 4 ? 0 : index % 10 < 6 ? 1 : 2;
        const edge = Math.floor(Math.random()*12), axis = edge % 3;
        const local = [Math.random()-.5, Math.random()-.5, Math.random()-.5];
        if (roles[index] === 0) {
          local[(axis+1)%3] = Math.floor(edge/3)%2 ? .5 : -.5;
          local[(axis+2)%3] = Math.floor(edge/6)%2 ? .5 : -.5;
        }
        boxes.set(local, index*3);
      }
    }
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute("position", new THREE.BufferAttribute(positions, 3));
    geometry.setAttribute("aRandom", new THREE.BufferAttribute(randomness, 1));
    geometry.setAttribute("aBox", new THREE.BufferAttribute(boxes, 3));
    geometry.setAttribute("aNode", new THREE.BufferAttribute(nodes, 1));
    geometry.setAttribute("aRole", new THREE.BufferAttribute(roles, 1));
    const seed = Math.random() * 100;
    const materials: THREE.ShaderMaterial[] = [];
    const particleLayers: THREE.Points[] = [];
    for (let layer = 0; layer < (smallScreen ? 1 : 2); layer++) {
      const material = new THREE.ShaderMaterial({
        vertexShader, fragmentShader,
        uniforms: {
          uTime: { value: 0 }, uSeed: { value: seed }, uLayer: { value: layer }, uPixelRatio: { value: 1 },
          uMorph: { value: 0 }, uSize: { value: 68 }, uPhase: { value: 0 }, uMotion: { value: 1 }, uActive: { value: 0 }, uCurve: { value: 0 },
          uViewport: { value: new THREE.Vector2(1440, 1000) }, uFrom: { value: new THREE.Vector2() }, uTo: { value: new THREE.Vector2() },
          uCenters: { value: PIPELINE_NODES.map(() => new THREE.Vector2()) },
        },
        transparent: true, depthWrite: false, depthTest: false,
      });
      materials.push(material);
      const points = new THREE.Points(geometry, material);
      // Shader displacement can extend beyond the original bounds.
      points.frustumCulled = false;
      particleLayers.push(points);
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
    let wasAnalyzing = false, began = 0, morph = 0, reportedStep = -2;
    let returnBegan = 0, returnMorph = 0;
    let layout = pipelineLayout(window.innerWidth, window.innerHeight);
    const ease = (value: number) => { const t = Math.max(0, Math.min(1, value)); return t*t*t*(t*(t*6-15)+10); };
    function render() {
      if (mode.current.analyzing && !wasAnalyzing) { began = elapsed; reportedStep = -2; }
      if (!mode.current.analyzing && wasAnalyzing) { returnBegan = elapsed; returnMorph = morph; }
      wasAnalyzing = mode.current.analyzing;
      const age = elapsed - began;
      morph = mode.current.analyzing ? (motion.matches ? 1 : ease(age/MORPH_SECONDS)) : motion.matches ? 0 : returnMorph*(1-ease((elapsed-returnBegan)/1.4));
      const processAge = Math.max(0, age-MORPH_SECONDS);
      const step = Math.floor(processAge/STEP_SECONDS) % PIPELINE_STEPS.length;
      const stage = PIPELINE_STEPS[step];
      const from = layout.nodes[nodeIndex(stage.id)], to = layout.nodes[nodeIndex(stage.next)];
      const phase = processAge % STEP_SECONDS;
      const visibleStep = !motion.matches && age < MORPH_SECONDS ? -1 : step;
      if (mode.current.analyzing && visibleStep !== reportedStep) { reportedStep = visibleStep; mode.current.onStageChange?.(visibleStep); }
      for (const material of materials) {
        const u = material.uniforms;
        u.uTime.value = elapsed; u.uMorph.value = morph; u.uSize.value = layout.size;
        u.uFrom.value.set(from.px, from.py); u.uTo.value.set(to.px, to.py);
        u.uPhase.value = phase; u.uMotion.value = motion.matches ? 0 : 1; u.uActive.value = nodeIndex(stage.id);
        u.uCurve.value = Math.abs(from.py-to.py)>30 ? 1 : 0;
      }
      dustMaterial.opacity = .22*(1-morph);
      particleLayers.forEach((points, index) => { points.visible = index === 0 || morph < .999; });
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
      const targetFps = mode.current.analyzing || morph > 0 ? 60 : 30;
      if (now - lastFrame < 1000 / targetFps - 0.5) return;
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
      layout = pipelineLayout(width, height);
      for (const material of materials) {
        material.uniforms.uPixelRatio.value = pixelRatio;
        material.uniforms.uViewport.value.set(width, height);
        layout.nodes.forEach((node, index) => material.uniforms.uCenters.value[index].set(node.px, node.py));
      }
      if (!document.hidden && !contextLost) render();
    }
    function pointerMove(event: PointerEvent) {
      if (motion.matches || mode.current.analyzing || event.pointerType !== "mouse") return;
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
    refresh.current = resume;
    const reducedTimer = setInterval(() => {
      if (motion.matches && mode.current.analyzing && !document.hidden && !contextLost) { elapsed += STEP_SECONDS; render(); }
    }, STEP_SECONDS*1000);
    resize(); resume();
    return () => {
      cancelAnimationFrame(frameId);
      clearInterval(reducedTimer); refresh.current = () => {};
      delete document.documentElement.dataset.particleRenderer;
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
