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

  const handleMouseMove = (e: React.MouseEvent<HTMLDivElement>) => {
    if (!containerRef.current) return;
    const rect = containerRef.current.getBoundingClientRect();
    const x = e.clientX - rect.left;
    const y = e.clientY - rect.top;
    setMousePos({ x, y });

    // Subtle 3D tilt physics (max 2.8 degrees)
    const centerX = rect.width / 2;
    const centerY = rect.height / 2;
    const tiltX = ((y - centerY) / centerY) * -2.4;
    const tiltY = ((x - centerX) / centerX) * 2.4;
    setTilt({ x: tiltX, y: tiltY });
  };

  const handleMouseLeave = () => {
    setIsHovered(false);
    setTilt({ x: 0, y: 0 });
  };

  const handleMouseEnter = () => {
    setIsHovered(true);
  };

  return (
    <div className="relative w-full max-w-xl mx-auto perspective-[1200px]">
      {/* SVG Optical Refraction and Chromatic Dispersion Filters */}
      <svg className="absolute w-0 h-0 pointer-events-none" aria-hidden="true">
        <defs>
          <filter id="apple-liquid-glass" colorInterpolationFilters="sRGB">
            <feTurbulence
              type="fractalNoise"
              baseFrequency="0.03"
              numOctaves="2"
              result="noise"
            />
            <feDisplacementMap
              in="SourceGraphic"
              in2="noise"
              scale="6"
              xChannelSelector="R"
              yChannelSelector="G"
              result="displaced"
            />
            <feColorMatrix
              in="displaced"
              type="matrix"
              values="1 0 0 0 0
                      0 0 0 0 0
                      0 0 0 0 0
                      0 0 0 1 0"
              result="red"
            />
            <feColorMatrix
              in="displaced"
              type="matrix"
              values="0 0 0 0 0
                      0 1 0 0 0
                      0 0 0 0 0
                      0 0 0 1 0"
              result="green"
            />
            <feColorMatrix
              in="displaced"
              type="matrix"
              values="0 0 0 0 0
                      0 0 0 0 0
                      0 0 1 0 0
                      0 0 0 1 0"
              result="blue"
            />
            <feOffset in="red" dx="-1" dy="0" result="redShift" />
            <feOffset in="blue" dx="1" dy="0" result="blueShift" />
            <feBlend in="redShift" in2="green" mode="screen" result="rg" />
            <feBlend in="rg" in2="blueShift" mode="screen" />
          </filter>
        </defs>
      </svg>

      {/* Ambient Liquid Backlight Glow */}
      <div
        className={`absolute -inset-1 rounded-[28px] bg-gradient-to-r from-amber-400/25 via-teal-300/20 to-purple-400/25 blur-xl pointer-events-none transition-all duration-500 ${
          isHovered || isFocused ? "opacity-100 scale-102" : "opacity-35"
        }`}
      />

      {/* Main Apple Ultra-Transparent Liquid Glass Pane */}
      <div
        ref={containerRef}
        onMouseMove={handleMouseMove}
        onMouseEnter={handleMouseEnter}
        onMouseLeave={handleMouseLeave}
        style={{
          transform: `rotateX(${tilt.x}deg) rotateY(${tilt.y}deg)`,
          transition: isHovered
            ? "transform 0.12s ease-out"
            : "transform 0.5s cubic-bezier(0.2, 0.8, 0.2, 1)",
          backdropFilter: "blur(28px) saturate(190%) contrast(105%) brightness(108%)",
          WebkitBackdropFilter: "blur(28px) saturate(190%) contrast(105%) brightness(108%)",
          backgroundColor: isFocused ? "rgba(255, 255, 255, 0.08)" : "rgba(255, 255, 255, 0.05)",
          boxShadow: isFocused
            ? "inset 0 1.5px 1.5px rgba(255, 255, 255, 0.75), inset 0 -1.5px 2px rgba(0, 0, 0, 0.35), inset 0 0 25px rgba(255, 255, 255, 0.08), 0 25px 60px rgba(0, 0, 0, 0.45)"
            : "inset 0 1.2px 1.2px rgba(255, 255, 255, 0.6), inset 0 -1px 1.5px rgba(0, 0, 0, 0.25), inset 0 0 20px rgba(255, 255, 255, 0.04), 0 18px 45px rgba(0, 0, 0, 0.35)",
        }}
        className={`relative rounded-[24px] p-4 transition-all duration-300 overflow-hidden border ${
          isFocused ? "border-white/40" : "border-white/20"
        }`}
      >
        {/* Subtle Liquid Prismatic Sheen passing through clear glass */}
        <div className="absolute inset-0 bg-gradient-to-b from-white/[0.12] via-transparent to-black/[0.08] pointer-events-none" />

        {/* Dynamic Specular Spotlight Tracking Cursor (Apple Physical Glass Reflection) */}
        <div
          className="absolute inset-0 pointer-events-none transition-opacity duration-300"
          style={{
            background: `radial-gradient(360px circle at ${mousePos.x}px ${mousePos.y}px, rgba(255, 255, 255, 0.3), rgba(255, 255, 255, 0.08) 35%, transparent 65%)`,
            opacity: isHovered ? 1 : 0.35,
          }}
        />

        {/* Chromatic Edge Dispersion Ray */}
        <div
          className="absolute inset-0 rounded-[24px] pointer-events-none"
          style={{
            background: `radial-gradient(420px circle at ${mousePos.x}px ${mousePos.y}px, rgba(160, 220, 255, 0.2), rgba(255, 170, 230, 0.16) 45%, transparent 75%)`,
          }}
        />

        {/* Content Container (Layered inside pure transparent glass) */}
        <div className="relative z-10">
          {/* Main Input Row */}
          <div className="flex items-center gap-3">
            {/* Liquid-Etched GitHub Icon */}
            <div className="text-white pl-1 shrink-0 drop-shadow-[0_0_10px_rgba(255,255,255,0.45)]">
              <svg className="w-5 h-5" viewBox="0 0 24 24" fill="currentColor">
                <path
                  fillRule="evenodd"
                  clipRule="evenodd"
                  d="M12 2C6.477 2 2 6.484 2 12.017c0 4.425 2.865 8.18 6.839 9.504.5.092.682-.217.682-.483 0-.237-.008-.868-.013-1.703-2.782.605-3.369-1.343-3.369-1.343-.454-1.158-1.11-1.466-1.11-1.466-.908-.62.069-.608.069-.608 1.003.07 1.53 1.032 1.53 1.032.892 1.53 2.341 1.088 2.91.832.092-.647.35-1.088.636-1.338-2.22-.253-4.555-1.113-4.555-4.951 0-1.093.39-1.988 1.029-2.688-.103-.253-.446-1.272.098-2.65 0 0 .84-.27 2.75 1.026A9.564 9.564 0 0112 6.844c.85.004 1.705.115 2.504.337 1.909-1.296 2.747-1.027 2.747-1.027.546 1.379.202 2.398.1 2.651.64.7 1.028 1.595 1.028 2.688 0 3.848-2.339 4.695-4.566 4.943.359.309.678.92.678 1.855 0 1.338-.012 2.419-.012 2.747 0 .268.18.58.688.482A10.019 10.019 0 0022 12.017C22 6.484 17.522 2 12 2z"
                />
              </svg>
            </div>

            {/* Crystal-Clear Transparent Input Field */}
            <input
              type="text"
              value={repoUrl}
              onChange={(e) => setRepoUrl(e.target.value)}
              onFocus={() => setIsFocused(true)}
              onBlur={() => setIsFocused(false)}
              onKeyDown={(e) => e.key === "Enter" && onAnalyze()}
              placeholder="Paste your GitHub repository link..."
              className="w-full bg-transparent text-xs text-white placeholder-zinc-300 outline-none font-medium tracking-wide selection:bg-amber-400/40"
            />

            {/* Liquid Jewel Analyze Button */}
            <button
              onClick={() => onAnalyze()}
              disabled={isAnalyzing}
              className="shrink-0 relative group/btn flex items-center gap-1.5 rounded-xl px-4 py-1.5 text-xs font-semibold text-white transition-all duration-200 cursor-pointer overflow-hidden disabled:opacity-60 shadow-[0_4px_20px_rgba(79,70,229,0.5)] hover:shadow-[0_8px_30px_rgba(99,102,241,0.7)] hover:scale-102 active:scale-95"
              style={{
                background:
                  "linear-gradient(135deg, rgba(79, 70, 229, 0.92), rgba(124, 58, 237, 0.92))",
                boxShadow:
                  "inset 0 1px 1px rgba(255, 255, 255, 0.6), inset 0 -1px 1px rgba(0, 0, 0, 0.3), 0 8px 25px rgba(99, 102, 241, 0.45)",
                backdropFilter: "blur(12px)",
              }}
            >
              {/* Glossy top reflection highlight */}
              <div className="absolute inset-x-0 top-0 h-1/2 bg-gradient-to-b from-white/40 to-transparent pointer-events-none" />

              {isAnalyzing ? (
                <>
                  <Loader2 className="w-3.5 h-3.5 animate-spin" />
                  <span>Analyzing...</span>
                </>
              ) : (
                <>
                  <span className="relative z-10">→ Analyze</span>
                </>
              )}
            </button>
          </div>

          {/* Liquid Glass Divider */}
          <div className="relative flex items-center justify-center my-2.5">
            <div className="h-[1px] bg-gradient-to-r from-transparent via-white/20 to-transparent w-full" />
            <span className="px-3 text-[10px] text-zinc-300 tracking-wider shrink-0 font-medium">
              or try an example
            </span>
            <div className="h-[1px] bg-gradient-to-r from-transparent via-white/20 to-transparent w-full" />
          </div>

          {/* Mini Liquid Glass Example Capsules */}
          <div className="flex flex-wrap items-center justify-center gap-2">
            {sampleRepos.map((item) => (
              <button
                key={item.name}
                onClick={() => {
                  setRepoUrl(item.url);
                  onAnalyze(item.url);
                }}
                className="group/pill relative flex items-center gap-1.5 rounded-full px-3 py-1 text-[11px] font-medium text-white transition-all duration-200 cursor-pointer overflow-hidden border border-white/20 hover:border-white/45 hover:scale-102 active:scale-95"
                style={{
                  background:
                    "linear-gradient(180deg, rgba(255, 255, 255, 0.12) 0%, rgba(255, 255, 255, 0.03) 100%)",
                  boxShadow:
                    "inset 0 1px 1px rgba(255, 255, 255, 0.45), 0 4px 12px rgba(0, 0, 0, 0.2)",
                  backdropFilter: "blur(16px)",
                  WebkitBackdropFilter: "blur(16px)",
                }}
              >
                {/* Micro Hover Flare */}
                <div className="absolute inset-0 opacity-0 group-hover/pill:opacity-100 bg-gradient-to-r from-transparent via-white/15 to-transparent transition-opacity duration-300 pointer-events-none" />

                <svg
                  className="w-3 h-3 text-zinc-300 group-hover/pill:text-amber-300 transition-colors"
                  viewBox="0 0 24 24"
                  fill="currentColor"
                >
                  <path
                    fillRule="evenodd"
                    clipRule="evenodd"
                    d="M12 2C6.477 2 2 6.484 2 12.017c0 4.425 2.865 8.18 6.839 9.504.5.092.682-.217.682-.483 0-.237-.008-.868-.013-1.703-2.782.605-3.369-1.343-3.369-1.343-.454-1.158-1.11-1.466-1.11-1.466-.908-.62.069-.608.069-.608 1.003.07 1.53 1.032 1.53 1.032.892 1.53 2.341 1.088 2.91.832.092-.647.35-1.088.636-1.338-2.22-.253-4.555-1.113-4.555-4.951 0-1.093.39-1.988 1.029-2.688-.103-.253-.446-1.272.098-2.65 0 0 .84-.27 2.75 1.026A9.564 9.564 0 0112 6.844c.85.004 1.705.115 2.504.337 1.909-1.296 2.747-1.027 2.747-1.027.546 1.379.202 2.398.1 2.651.64.7 1.028 1.595 1.028 2.688 0 3.848-2.339 4.695-4.566 4.943.359.309.678.92.678 1.855 0 1.338-.012 2.419-.012 2.747 0 .268.18.58.688.482A10.019 10.019 0 0022 12.017C22 6.484 17.522 2 12 2z"
                  />
                </svg>
                <span>{item.name}</span>
              </button>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
