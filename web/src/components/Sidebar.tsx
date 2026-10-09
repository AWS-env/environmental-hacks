"use client";

import React, { useState } from "react";
import {
  Search,
  BookOpen,
  ShoppingBag,
} from "lucide-react";

export default function Sidebar() {
  const [activeItem, setActiveItem] = useState("bot");

  return (
    <aside className="fixed left-3.5 top-3.5 bottom-3.5 w-[50px] rounded-full bg-[#0a0d11] shadow-[0_20px_50px_rgba(0,0,0,0.85)] flex flex-col items-center justify-between py-4 z-50 border border-white/[0.08] select-none backdrop-blur-xl">
      {/* Top Icons */}
      <div className="flex flex-col items-center gap-3.5 w-full">
        {/* Search */}
        <button
          onClick={() => setActiveItem("search")}
          title="Search (Ctrl+K)"
          className={`relative group flex items-center justify-center w-8 h-8 rounded-full transition-all duration-200 ${
            activeItem === "search" ? "text-white" : "text-zinc-400 hover:text-white"
          }`}
        >
          <Search strokeWidth={2.2} className="w-[17px] h-[17px]" />
          <span className="absolute left-[54px] px-2.5 py-1 text-xs font-medium text-white bg-black/90 rounded-md border border-white/10 shadow-xl whitespace-nowrap pointer-events-none opacity-0 group-hover:opacity-100 transition-opacity z-50">
            Search
          </span>
        </button>

        {/* Active Kimi / Bot Avatar */}
        <button
          onClick={() => setActiveItem("bot")}
          title="Workspace / Chat"
          className="relative group flex items-center justify-center w-8 h-8 rounded-full transition-all duration-200"
        >
          <div className={`transition-all duration-200 ${activeItem === "bot" ? "text-white scale-105" : "text-zinc-400 hover:text-white"}`}>
            <svg
              viewBox="0 0 24 24"
              fill="currentColor"
              className="w-[20px] h-[20px]"
            >
              <circle cx="5" cy="11" r="2" />
              <circle cx="19" cy="11" r="2" />
              <rect x="11" y="2" width="2" height="3" rx="1" />
              <rect x="6" y="5" width="12" height="13" rx="5" />
              <circle cx="9.5" cy="11.5" r="1.5" fill="#0a0d11" />
              <circle cx="14.5" cy="11.5" r="1.5" fill="#0a0d11" />
              <rect x="9.5" y="14.5" width="5" height="1.2" rx="0.6" fill="#0a0d11" />
            </svg>
          </div>
          <span className="absolute left-[54px] px-2.5 py-1 text-xs font-medium text-white bg-black/90 rounded-md border border-white/10 shadow-xl whitespace-nowrap pointer-events-none opacity-0 group-hover:opacity-100 transition-opacity z-50">
            Kimi Workspace
          </span>
        </button>

        {/* Key / Wand icon */}
        <button
          onClick={() => setActiveItem("key")}
          title="API Keys & Secrets"
          className={`relative group flex items-center justify-center w-8 h-8 rounded-full transition-all duration-200 ${
            activeItem === "key" ? "text-white" : "text-zinc-400 hover:text-white"
          }`}
        >
          <svg
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2.2"
            strokeLinecap="round"
            strokeLinejoin="round"
            className="w-[17px] h-[17px]"
          >
            <circle cx="7.5" cy="15.5" r="5" />
            <path d="m11 12 8-8" />
            <path d="m16 4 3 3" />
            <path d="m14 6 2 2" />
          </svg>
          <span className="absolute left-[54px] px-2.5 py-1 text-xs font-medium text-white bg-black/90 rounded-md border border-white/10 shadow-xl whitespace-nowrap pointer-events-none opacity-0 group-hover:opacity-100 transition-opacity z-50">
            Keys & Access
          </span>
        </button>

        {/* Histogram / Metrics icon */}
        <button
          onClick={() => setActiveItem("metrics")}
          title="Analytics & Logs"
          className={`relative group flex items-center justify-center w-8 h-8 rounded-full transition-all duration-200 ${
            activeItem === "metrics" ? "text-white" : "text-zinc-400 hover:text-white"
          }`}
        >
          <svg
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2.2"
            strokeLinecap="round"
            strokeLinejoin="round"
            className="w-[17px] h-[17px]"
          >
            <rect width="18" height="18" x="3" y="3" rx="4" />
            <path d="M8 17v-4" />
            <path d="M12 17v-7" />
            <path d="M16 17v-2" />
          </svg>
          <span className="absolute left-[54px] px-2.5 py-1 text-xs font-medium text-white bg-black/90 rounded-md border border-white/10 shadow-xl whitespace-nowrap pointer-events-none opacity-0 group-hover:opacity-100 transition-opacity z-50">
            Analytics
          </span>
        </button>

        {/* Chat / Pulse waveform bubble */}
        <button
          onClick={() => setActiveItem("pulse")}
          title="Discussions & Prompts"
          className={`relative group flex items-center justify-center w-8 h-8 rounded-full transition-all duration-200 ${
            activeItem === "pulse" ? "text-white" : "text-zinc-400 hover:text-white"
          }`}
        >
          <svg
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2.2"
            strokeLinecap="round"
            strokeLinejoin="round"
            className="w-[17px] h-[17px]"
          >
            <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
            <path d="M8 10h1.5l1.5-2 2 4 1.5-2H17" />
          </svg>
          <span className="absolute left-[54px] px-2.5 py-1 text-xs font-medium text-white bg-black/90 rounded-md border border-white/10 shadow-xl whitespace-nowrap pointer-events-none opacity-0 group-hover:opacity-100 transition-opacity z-50">
            Prompts & Threads
          </span>
        </button>

        {/* 4-petal App grid / Hub */}
        <button
          onClick={() => setActiveItem("grid")}
          title="Apps & Integrations"
          className={`relative group flex items-center justify-center w-8 h-8 rounded-full transition-all duration-200 ${
            activeItem === "grid" ? "text-white" : "text-zinc-400 hover:text-white"
          }`}
        >
          <svg
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2.2"
            strokeLinecap="round"
            strokeLinejoin="round"
            className="w-[17px] h-[17px]"
          >
            <circle cx="8" cy="8" r="2.5" />
            <circle cx="16" cy="8" r="2.5" />
            <circle cx="8" cy="16" r="2.5" />
            <circle cx="16" cy="16" r="2.5" />
          </svg>
          <span className="absolute left-[54px] px-2.5 py-1 text-xs font-medium text-white bg-black/90 rounded-md border border-white/10 shadow-xl whitespace-nowrap pointer-events-none opacity-0 group-hover:opacity-100 transition-opacity z-50">
            Workspaces
          </span>
        </button>
      </div>

      {/* Bottom Icons */}
      <div className="flex flex-col items-center gap-3 w-full">
        {/* Documentation Book */}
        <button
          onClick={() => setActiveItem("book")}
          title="Documentation"
          className={`relative group flex items-center justify-center w-8 h-8 rounded-full transition-all duration-200 ${
            activeItem === "book" ? "text-white" : "text-zinc-400 hover:text-white"
          }`}
        >
          <BookOpen strokeWidth={2.2} className="w-[17px] h-[17px]" />
          <span className="absolute left-[54px] px-2.5 py-1 text-xs font-medium text-white bg-black/90 rounded-md border border-white/10 shadow-xl whitespace-nowrap pointer-events-none opacity-0 group-hover:opacity-100 transition-opacity z-50">
            Documentation
          </span>
        </button>

        {/* Store / Marketplace */}
        <button
          onClick={() => setActiveItem("store")}
          title="Plugin Store"
          className={`relative group flex items-center justify-center w-8 h-8 rounded-full transition-all duration-200 ${
            activeItem === "store" ? "text-white" : "text-zinc-400 hover:text-white"
          }`}
        >
          <ShoppingBag strokeWidth={2.2} className="w-[17px] h-[17px]" />
          <span className="absolute left-[54px] px-2.5 py-1 text-xs font-medium text-white bg-black/90 rounded-md border border-white/10 shadow-xl whitespace-nowrap pointer-events-none opacity-0 group-hover:opacity-100 transition-opacity z-50">
            Marketplace
          </span>
        </button>

        {/* 3D Iridescent Glossy Sphere */}
        <button
          onClick={() => setActiveItem("gradient")}
          title="AI Studio & Settings"
          className="relative group mt-0.5"
        >
          <div className="w-[22px] h-[22px] rounded-full bg-gradient-to-tr from-[#ff3b69] via-[#892be2] to-[#00f2fe] shadow-[inset_1.5px_1.5px_3px_rgba(255,255,255,0.7),0_0_10px_rgba(137,43,226,0.5)] hover:scale-110 active:scale-95 transition-all duration-200 cursor-pointer" />
          <span className="absolute left-[54px] px-2.5 py-1 text-xs font-medium text-white bg-black/90 rounded-md border border-white/10 shadow-xl whitespace-nowrap pointer-events-none opacity-0 group-hover:opacity-100 transition-opacity z-50">
            AI Engine
          </span>
        </button>
      </div>
    </aside>
  );
}
