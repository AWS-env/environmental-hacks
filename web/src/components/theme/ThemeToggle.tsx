"use client";

import { useEffect } from "react";
import { SYSTEM_DARK, THEME_STORAGE_KEY } from "./boot";
import type { Theme } from "./boot";

function storedTheme(): Theme | null {
  try {
    const t = localStorage.getItem(THEME_STORAGE_KEY);
    return t === "light" || t === "dark" ? t : null;
  } catch {
    return null;
  }
}

/**
 * Flips between the day and night looks and remembers the choice. Until the
 * visitor picks one, the page follows the system setting, live.
 *
 * The icon and label come from CSS (`dark:`), so the server markup never has to
 * know the theme and hydration always matches.
 */
export default function ThemeToggle() {
  useEffect(() => {
    const media = window.matchMedia(SYSTEM_DARK);
    const follow = () => {
      if (!storedTheme()) document.documentElement.dataset.theme = media.matches ? "dark" : "light";
    };
    media.addEventListener("change", follow);
    return () => media.removeEventListener("change", follow);
  }, []);

  const toggle = () => {
    const next: Theme = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    try {
      localStorage.setItem(THEME_STORAGE_KEY, next);
    } catch {
      // Private mode or blocked storage: the switch still applies to this visit.
    }
  };

  return (
    <button
      type="button"
      onClick={toggle}
      className="flex h-[30px] w-[30px] items-center justify-center rounded-lg border border-line bg-surface-2 text-ink-2 transition-colors hover:bg-surface-3 hover:text-ink"
    >
      <span className="sr-only dark:hidden">Switch to dark theme</span>
      <span className="sr-only hidden dark:inline">Switch to light theme</span>
      {/* Moon by day, sun by night: the icon shows where the click goes. */}
      <svg aria-hidden="true" className="h-4 w-4 dark:hidden" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
        <path strokeLinecap="round" strokeLinejoin="round" d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z" />
      </svg>
      <svg aria-hidden="true" className="hidden h-4 w-4 dark:block" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
        <circle cx="12" cy="12" r="4" />
        <path strokeLinecap="round" d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" />
      </svg>
    </button>
  );
}
