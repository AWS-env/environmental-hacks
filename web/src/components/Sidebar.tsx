"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { House, LayoutDashboard, Leaf, Moon, Sun, Zap } from "lucide-react";
import { THEME_STORAGE_KEY, type Theme } from "@/components/theme/boot";
import { useRouteTransition } from "@/components/transition/RouteTransition";
import "./sidebar.css";

const pages = [
  { href: "/home", label: "Home", icon: House },
  { href: "/overview", label: "Overview", icon: LayoutDashboard },
  { href: "/environment", label: "Environment (run-it)", icon: Zap },
  { href: "/reports", label: "Carbon reports", icon: Leaf },
];

export default function Sidebar() {
  const pathname = usePathname();
  const transition = useRouteTransition();
  const toggleTheme = () => {
    const next: Theme = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    try { localStorage.setItem(THEME_STORAGE_KEY, next); } catch { /* Private browsing may disable storage. */ }
  };
  return (
    <aside data-route-keep className="workspace-sidebar" aria-label="Workspace navigation">
      <nav className="workspace-sidebar__pages" aria-label="Pages">
        {pages.map(({ href, label, icon: Icon }) => (
          <Link key={href} href={href} aria-label={label} title={label}
            aria-current={pathname.startsWith(href) ? "page" : undefined}
            onNavigate={(event) => transition?.intercept(event, href)} className="workspace-sidebar__control">
            <Icon size={17} strokeWidth={1.7} />
            <span className="workspace-sidebar__tooltip">{label}</span>
          </Link>
        ))}
      </nav>
      <div className="workspace-sidebar__bottom">
        <button type="button" onClick={toggleTheme} aria-label="Toggle light / dark mode" title="Toggle light / dark mode" className="workspace-sidebar__control">
          <Moon size={17} strokeWidth={1.7} className="block dark:hidden" />
          <Sun size={17} strokeWidth={1.7} className="hidden dark:block" />
          <span className="workspace-sidebar__tooltip">Light / dark</span>
        </button>
        <Link href="/login" aria-label="Account / Sign In" title="Account / Sign In"
          onNavigate={(event) => transition?.intercept(event, "/login")} className="workspace-sidebar__control workspace-sidebar__account">
          <span aria-hidden="true" className="workspace-sidebar__orb" />
          <span className="workspace-sidebar__tooltip">Account / Sign In</span>
        </Link>
      </div>
    </aside>
  );
}
