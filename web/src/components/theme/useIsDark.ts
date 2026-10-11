"use client";

import { useEffect, useState } from "react";
import { currentTheme } from "@/components/theme/boot";

/**
 * Whether the page is showing the night theme. Starts false so server and client
 * markup match, then follows `<html data-theme>` (the sidebar toggle changes it).
 */
export function useIsDark(): boolean {
  const [isDark, setIsDark] = useState(false);

  useEffect(() => {
    const sync = () => setIsDark(currentTheme() === "dark");
    sync();
    const observer = new MutationObserver(sync);
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
    return () => observer.disconnect();
  }, []);

  return isDark;
}
