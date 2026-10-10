"use client";

import React, { useState, useRef, useEffect } from "react";
import { Loader2 } from "lucide-react";

interface LiquidGlassInputProps {
  repoUrl: string;
  setRepoUrl: (url: string) => void;
  onAnalyze: (url?: string) => void;
  isAnalyzing: boolean;
  sampleRepos: { name: string; url: string }[];
}

interface Ripple {
  x: number;
  y: number;
  id: number;
}

export default function LiquidGlassInput({
  repoUrl,
  setRepoUrl,
  onAnalyze,
  isAnalyzing,
  sampleRepos,
}: LiquidGlassInputProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [mousePos, setMousePos] = useState({ x: 280, y: 50 });
  const [isHovered, setIsHovered] = useState(false);
  const [isFocused, setIsFocused] = useState(false);
  const [tilt, setTilt] = useState({ x: 0, y: 0 });
  const [ripples, setRipples] = useState<Ripple[]>([]);

  // Elastic Spring tracking for viscous fluid inertia
  const targetTilt = useRef({ x: 0, y: 0 });
  const currentTilt = useRef({ x: 0, y: 0 });

  const startSpring = useRef<() => void>(() => {});
  const reducedMotion = useRef(true);
  const rippleTimers = useRef(new Set<ReturnType<typeof setTimeout>>());
  const rippleSequence = useRef(0);

  useEffect(() => {
    const motion = window.matchMedia("(prefers-reduced-motion: reduce)");
    const timers = rippleTimers.current;
    let animId = 0;
    function updateSpring() {
      animId = 0;
      if (document.hidden || reducedMotion.current) return;
      currentTilt.current.x += (targetTilt.current.x - currentTilt.current.x) * 0.1;
      currentTilt.current.y += (targetTilt.current.y - currentTilt.current.y) * 0.1;
      setTilt({ x: currentTilt.current.x, y: currentTilt.current.y });
      if (Math.abs(targetTilt.current.x - currentTilt.current.x) +
          Math.abs(targetTilt.current.y - currentTilt.current.y) > 0.008) {
        animId = requestAnimationFrame(updateSpring);
      }
    }
    startSpring.current = () => {
      if (!animId && !document.hidden && !reducedMotion.current) {
        animId = requestAnimationFrame(updateSpring);
      }
    };
    function onMotionChange() {
      reducedMotion.current = motion.matches;
      if (motion.matches) {
        cancelAnimationFrame(animId);
        animId = 0;
        targetTilt.current = { x: 0, y: 0 };
        currentTilt.current = { x: 0, y: 0 };
        setTilt({ x: 0, y: 0 });
      }
    }
    reducedMotion.current = motion.matches;
    motion.addEventListener("change", onMotionChange);
    document.addEventListener("visibilitychange", startSpring.current);
    const resumeSpring = startSpring.current;
    return () => {
      cancelAnimationFrame(animId);
      timers.forEach(clearTimeout);
      timers.clear();
      motion.removeEventListener("change", onMotionChange);
      document.removeEventListener("visibilitychange", resumeSpring);
      startSpring.current = () => {};
    };
  }, []);

  const handleMouseMove = (e: React.MouseEvent<HTMLDivElement>) => {
    if (!containerRef.current || reducedMotion.current) return;
    const rect = containerRef.current.getBoundingClientRect();
    const x = e.clientX - rect.left;
    const y = e.clientY - rect.top;
    setMousePos({ x, y });

    const centerX = rect.width / 2;
    const centerY = rect.height / 2;
    targetTilt.current = {
      x: ((y - centerY) / centerY) * -3.0,
      y: ((x - centerX) / centerX) * 3.0,
    };
    startSpring.current();
  };

  const handleMouseLeave = () => {
    setIsHovered(false);
    targetTilt.current = { x: 0, y: 0 };
    startSpring.current();
  };

  const handleMouseEnter = () => {
    setIsHovered(true);
  };

  const triggerLiquidRipple = (e: React.MouseEvent<HTMLDivElement>) => {
    if (!containerRef.current || reducedMotion.current) return;
    const rect = containerRef.current.getBoundingClientRect();
    const x = e.clientX - rect.left;
    const y = e.clientY - rect.top;
    const newRipple = { x, y, id: ++rippleSequence.current };
    setRipples((prev) => [...prev.slice(-3), newRipple]);

    const timer = setTimeout(() => {
      setRipples((prev) => prev.filter((r) => r.id !== newRipple.id));
      rippleTimers.current.delete(timer);
    }, 1100);
    rippleTimers.current.add(timer);
  };

  // Gelatin surface tension morphing border radius
  const dynamicBorderRadius = `${28 + tilt.y * 1.4}px ${26 - tilt.x * 1.4}px ${28 - tilt.y * 1.4}px ${26 + tilt.x * 1.4}px`;

  return (
    <div className="relative w-full max-w-[820px] mx-auto perspective-[1200px]">
      {/* Living Liquid Refraction Filter */}
      <svg className="absolute w-0 h-0 pointer-events-none" aria-hidden="true">
        <defs>
          <filter id="pure-liquid-optics" colorInterpolationFilters="sRGB" x="-20%" y="-20%" width="140%" height="140%">
            <feTurbulence
              type="fractalNoise"
              baseFrequency="0.016 0.02"
              numOctaves="2"
              result="noise"
            >

            </feTurbulence>
            <feDisplacementMap
              in="SourceGraphic"
              in2="noise"
              scale="8"
              xChannelSelector="R"
              yChannelSelector="G"
              result="displaced"
            />
          </filter>
        </defs>
      </svg>

      {/* Subtle Pure Clear Water Ambient Glow */}
      <div
        className={`absolute -inset-1 rounded-[30px] bg-black/10 blur-xl pointer-events-none transition-all duration-700 ${
          isHovered || isFocused ? "opacity-100 scale-102" : "opacity-25"
        }`}
      />

      {/* Main ABSOLUTELY TRANSPARENT Liquid Glass Lens */}
      <div
        ref={containerRef}
        onMouseMove={handleMouseMove}
        onMouseEnter={handleMouseEnter}
        onMouseLeave={handleMouseLeave}
        onClick={triggerLiquidRipple}
        style={{
          transform: `rotateX(${tilt.x}deg) rotateY(${tilt.y}deg) scale(${isHovered ? 1.015 : 1})`,
          borderRadius: dynamicBorderRadius,
          transition: "transform 0.08s ease-out, border-radius 0.2s ease-out, box-shadow 0.3s ease",

        }}
        className={`liquid-panel repo-entry relative p-5 md:p-8 overflow-hidden border transition-all cursor-text select-none ${
          isFocused ? "border-white/25" : "border-white/10"
        }`}
      >
        <div aria-hidden="true" className="liquid-flow absolute inset-0 pointer-events-none" />
        {/* Fluid Water Top Meniscus Reflection */}
        <div className="absolute inset-0 bg-gradient-to-b from-white/[0.035] via-transparent to-black/[0.03] pointer-events-none" />

        {/* Dynamic Specular Water Caustic Highlight tracking cursor */}
        <div
          className="absolute inset-0 pointer-events-none transition-opacity duration-300"
          style={{
            background: `radial-gradient(340px circle at ${mousePos.x}px ${mousePos.y}px, rgba(255, 255, 255, 0.055) 0%, rgba(255, 255, 255, 0.012) 35%, transparent 65%)`,
            opacity: isHovered ? 0.65 : 0.15,
          }}
        />

        {/* Expanding Liquid Droplet Ripples on Click */}
        {ripples.map((r) => (
          <span
            key={r.id}
            className="liquid-ripple absolute rounded-full pointer-events-none"
            style={{
              left: r.x - 25,
              top: r.y - 25,
              width: 50,
              height: 50,
              background: "radial-gradient(circle, rgba(255,255,255,0.14) 0%, transparent 70%)",
              boxShadow: "0 0 18px rgba(255,255,255,0.12)",
            }}
          />
        ))}

        {/* Content Layer (Float effortlessly over the liquid glass lens) */}
        <div className="relative z-10">
          {/* Main Input Row */}
          <div className="flex flex-wrap sm:flex-nowrap items-center gap-4">
            {/* Polished Neutral White GitHub Icon */}
            <div className="text-white pl-1 shrink-0 drop-shadow-[0_0_10px_rgba(255,255,255,0.5)]">
              <svg className="w-6 h-6" viewBox="0 0 24 24" fill="currentColor">
                <path
                  fillRule="evenodd"
                  clipRule="evenodd"
                  d="M12 2C6.477 2 2 6.484 2 12.017c0 4.425 2.865 8.18 6.839 9.504.5.092.682-.217.682-.483 0-.237-.008-.868-.013-1.703-2.782.605-3.369-1.343-3.369-1.343-.454-1.158-1.11-1.466-1.11-1.466-.908-.62.069-.608.069-.608 1.003.07 1.53 1.032 1.53 1.032.892 1.53 2.341 1.088 2.91.832.092-.647.35-1.088.636-1.338-2.22-.253-4.555-1.113-4.555-4.951 0-1.093.39-1.988 1.029-2.688-.103-.253-.446-1.272.098-2.65 0 0 .84-.27 2.75 1.026A9.564 9.564 0 0112 6.844c.85.004 1.705.115 2.504.337 1.909-1.296 2.747-1.027 2.747-1.027.546 1.379.202 2.398.1 2.651.64.7 1.028 1.595 1.028 2.688 0 3.848-2.339 4.695-4.566 4.943.359.309.678.92.678 1.855 0 1.338-.012 2.419-.012 2.747 0 .268.18.58.688.482A10.019 10.019 0 0022 12.017C22 6.484 17.522 2 12 2z"
                />
              </svg>
            </div>

            {/* Completely Clear Text Input */}
            <input
              type="text"
              aria-label="GitHub repository URL"
              value={repoUrl}
              onChange={(e) => setRepoUrl(e.target.value)}
              onFocus={() => setIsFocused(true)}
              onBlur={() => setIsFocused(false)}
              onKeyDown={(e) => e.key === "Enter" && onAnalyze()}
              placeholder="Paste your GitHub repository link..."
              className="min-w-0 flex-1 bg-transparent py-3 text-base md:text-lg text-white placeholder-zinc-100 outline-none font-medium selection:bg-white/20"
            />

            {/* Liquid Droplet Analyze Button */}
            <button
              onClick={(e) => {
                e.stopPropagation();
                onAnalyze();
              }}
              disabled={isAnalyzing}
              className="liquid-glass-button shrink-0 relative group/btn flex items-center gap-1.5 rounded-xl px-6 py-3.5 text-base font-semibold text-white transition-all duration-200 cursor-pointer overflow-hidden disabled:opacity-60 shadow-sm hover:shadow-[0_6px_20px_rgba(0,0,0,0.2)] hover:scale-103 active:scale-95 border border-white/15"
            >
              <div className="absolute inset-x-0 top-0 h-1/2 bg-gradient-to-b from-white/[0.06] to-transparent pointer-events-none" />

              {isAnalyzing ? (
                <>
                  <Loader2 className="w-3.5 h-3.5 animate-spin" />
                  <span>Analyzing...</span>
                </>
              ) : (
                <>
                  <span className="relative z-10 flex items-center gap-1">
                    <span>→</span>
                    <span>Analyze</span>
                  </span>
                </>
              )}
            </button>
          </div>

          {/* Liquid Divider */}
          <div className="relative flex items-center justify-center my-6">
            <div className="h-[1px] bg-gradient-to-r from-transparent via-white/25 to-transparent w-full" />
            <span className="px-4 text-sm text-zinc-100 tracking-wider shrink-0 font-medium bg-transparent drop-shadow-sm">
              or try an example
            </span>
            <div className="h-[1px] bg-gradient-to-r from-transparent via-white/25 to-transparent w-full" />
          </div>

          {/* Liquid Example Capsules */}
          <div className="flex flex-wrap items-center justify-center gap-2">
            {sampleRepos.map((item) => (
              <button
                key={item.name}
                onClick={(e) => {
                  e.stopPropagation();
                  setRepoUrl(item.url);
                  onAnalyze(item.url);
                }}
                className="liquid-glass-button group/pill relative flex items-center gap-1.5 rounded-full px-4 py-2 text-sm font-medium text-white transition-all duration-200 cursor-pointer overflow-hidden border border-white/10 hover:border-white/25 hover:scale-104 active:scale-95 shadow-sm"
              >
                <svg
                  className="w-3 h-3 text-zinc-300 group-hover/pill:text-white transition-colors"
                  viewBox="0 0 24 24"
                  fill="currentColor"
                >
                  <path
                    fillRule="evenodd"
                    clipRule="evenodd"
                    d="M12 2C6.477 2 2 6.484 2 12.017c0 4.425 2.865 8.18 6.839 9.504.5.092.682-.217.682-.483 0-.237-.008-.868-.013-1.703-2.782.605-3.369-1.343-3.369-1.343-.454-1.158-1.11-1.466-1.11-1.466-.908-.62.069-.608.069-.608 1.003.07 1.53 1.032 1.53 1.032.892 1.53 2.341 1.088 2.91.832.092-.647.35-1.088.636-1.338-2.22-.253-4.555-1.113-4.555-4.951 0-1.093.39-1.988 1.029-2.688-.103-.253-.446-1.272.098-2.65 0 0 .84-.27 2.75 1.026A9.564 9.564 0 0112 6.844c.85.004 1.705.115 2.504.337 1.909-1.296 2.747-1.027 2.747-1.027.546 1.379.202 2.398.1 2.651.64.7 1.028 1.595 1.028 2.688 0 3.848-2.339 4.695-4.566 4.943.359.309.678.92.678 1.855 0 1.338-.012 2.419-.012 2.747 0 .268.18.58.688.482A10.019 10.019 0 0022 12.017C22 6.484 17.522 2 12 2z"
                  />
                </svg>
                <span className="relative z-10">{item.name}</span>
              </button>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
