"use client";

import React, { useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  Crosshair,
  BookOpen,
  Sun,
  Moon,
  Leaf,
  UserRound,
} from "lucide-react";
import { THEME_STORAGE_KEY } from "@/components/theme/boot";
import type { Theme } from "@/components/theme/boot";
import { useRouteTransition } from "@/components/transition/RouteTransition";

// Colours come from the theme tokens (globals.css), so the capsule reads on both the day sky and the night page.
const item = "relative group flex items-center justify-center w-8 h-8 rounded-full transition-all duration-200";
const idle = "text-ink-3 hover:text-ink";
const active = "text-accent-strong scale-105";
const tip =
  "absolute left-[54px] px-2.5 py-1 text-xs font-medium text-page bg-ink rounded-md border border-line shadow-xl whitespace-nowrap pointer-events-none opacity-0 group-hover:opacity-100 transition-opacity z-50";

export default function Sidebar() {
  const pathname = usePathname();
  const [activeOverride, setActiveOverride] = useState<string | null>(null);

  const toggleTheme = () => {
    const current = document.documentElement.dataset.theme;
    const next: Theme = current === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    try {
      localStorage.setItem(THEME_STORAGE_KEY, next);
    } catch {
      // Ignore storage errors in private browsing
    }
  };

  const routeItem =
    pathname === "/"
      ? "overview"
      : pathname.startsWith("/home")
      ? "bot"
      : pathname.startsWith("/reports")
      ? "reports"
      : pathname.startsWith("/login")
      ? "login"
      : "overview";

  const activeItem = activeOverride ?? routeItem;

  // Highlights the destination immediately, while the route transition plays.
  const transition = useRouteTransition();
  const go = (href: string, item: string) => (event: { preventDefault(): void }) => {
    setActiveOverride(item);
    transition?.intercept(event, href);
  };

  return (
    <aside data-route-keep className="sidebar-capsule fixed left-3.5 top-3.5 bottom-3.5 w-[50px] liquid-glass rounded-full flex flex-col items-center justify-between py-4 z-50 border select-none">
      {/* Top Icons */}
      <div className="flex flex-col items-center gap-3.5 w-full">
        {/* Project / Overview Crosshair */}
        <Link
          href="/"
          onNavigate={go("/", "overview")}
          title="Project Overview"
          className={`${item} ${activeItem === "overview" ? active : idle}`}
        >
          <Crosshair strokeWidth={2.2} className="w-[18px] h-[18px]" />
          <span className={tip}>Overview</span>
        </Link>

        {/* Analyze a repository */}
        <Link
          href="/home"
          onNavigate={go("/home", "bot")}
          title="Analyze a repository"
          className={item}
        >
          <div className={`transition-all duration-200 ${activeItem === "bot" ? "text-ink scale-105" : idle}`}>
            <svg
              viewBox="0 0 24 24"
              fill="currentColor"
              className="w-[20px] h-[20px]"
            >
              <circle cx="5" cy="11" r="2" />
              <circle cx="19" cy="11" r="2" />
              <rect x="11" y="2" width="2" height="3" rx="1" />
              <rect x="6" y="5" width="12" height="13" rx="5" />
              {/* The face cuts through to the page colour, so it stays visible on both themes. */}
              <circle cx="9.5" cy="11.5" r="1.5" fill="var(--page)" />
              <circle cx="14.5" cy="11.5" r="1.5" fill="var(--page)" />
              <rect x="9.5" y="14.5" width="5" height="1.2" rx="0.6" fill="var(--page)" />
            </svg>
          </div>
          <span className={tip}>Analyze a repo</span>
        </Link>

        {/* Environmental Reports & Impact */}
        <Link
          href="/reports"
          onNavigate={go("/reports", "reports")}
          title="Sustainability Reports & Impact"
          className={`${item} ${activeItem === "reports" ? active : idle}`}
        >
          <Leaf strokeWidth={2.2} className="w-[17px] h-[17px]" />
          <span className={tip}>Carbon Reports</span>
        </Link>
      </div>

      {/* Bottom Icons */}
      <div className="flex flex-col items-center gap-3 w-full">
        {/* Subtle Divider */}
        <div className="w-5 h-px bg-line my-0.5" />

        {/* Theme Toggle (Day / Night) */}
        <button
          type="button"
          onClick={toggleTheme}
          title="Toggle light / dark mode"
          className={`${item} ${idle}`}
        >
          {/* Moon by day, Sun by night: icon indicates the destination theme */}
          <Moon strokeWidth={2.2} className="w-[17px] h-[17px] block dark:hidden" />
          <Sun strokeWidth={2.2} className="w-[17px] h-[17px] hidden dark:block" />
          <span className={tip}>Toggle Theme</span>
        </button>

        {/* Documentation Book */}
        <a
          href="https://github.com/AWS-env/environmental-hacks"
          target="_blank"
          rel="noreferrer"
          title="Documentation"
          className={`${item} ${idle}`}
        >
          <BookOpen strokeWidth={2.2} className="w-[17px] h-[17px]" />
          <span className={tip}>Documentation</span>
        </a>

        {/* Account / User Avatar */}
        <Link
          href="/login"
          onNavigate={go("/login", "login")}
          title="Account / Sign In"
          className={`relative group mt-0.5 flex items-center justify-center w-7 h-7 rounded-full bg-surface-3 border transition-all duration-200 ${
            activeItem === "login"
              ? "border-accent-strong text-accent-strong shadow-[0_0_8px_var(--accent-line)]"
              : "border-line text-ink-2 hover:text-ink hover:border-ink-4"
          }`}
        >
          <UserRound strokeWidth={2.2} className="w-[15px] h-[15px]" />
          <span className={tip}>Account / Sign In</span>
        </Link>
      </div>
    </aside>
  );
}
