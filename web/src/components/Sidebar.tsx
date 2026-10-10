"use client";

import { useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { House, LayoutDashboard, Box, List, ChartLine, Gauge, Activity, BookOpen, ShoppingBag } from "lucide-react";

const navigation = [
  { label: "Home", icon: House, href: "/home" },
  { label: "Overview", icon: LayoutDashboard, href: "/overview" },
  { label: "Deployments", icon: Box },
  { label: "Logs", icon: List },
  { label: "Analytics", icon: ChartLine },
  { label: "Speed Insights", icon: Gauge },
  { label: "Observability", icon: Activity },
];

export default function Sidebar() {
  const [activeItem, setActiveItem] = useState("");
  const pathname = usePathname();

  return (
    <aside className="fixed left-3.5 top-3.5 bottom-3.5 w-[50px] liquid-glass rounded-full shadow-[0_20px_50px_rgba(0,0,0,0.85)] flex flex-col items-center justify-between py-4 z-50 border border-white/[0.08] select-none">
      <nav aria-label="Main navigation" className="flex flex-col items-center gap-3.5 w-full">
        {navigation.map(({ label, icon: Icon, href }, index) => {
          const active = href ? pathname === href || (href === "/home" && pathname === "/") : activeItem === label;
          const className = `relative group flex items-center justify-center w-8 h-8 shrink-0 rounded-full transition-all duration-200 motion-reduce:transition-none focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white/70 ${
            active
              ? "text-white bg-white/[0.10] shadow-[inset_0_1px_0_rgba(255,255,255,0.15)]"
              : "text-zinc-400 hover:text-white hover:bg-white/[0.05]"
          }`;
          const content = (
            <>
              <Icon strokeWidth={1.9} className="w-[18px] h-[18px]" aria-hidden="true" />
              <span aria-hidden="true" className="absolute left-[46px] px-3 py-1.5 text-xs font-medium text-white bg-black/90 rounded-lg border border-white/10 shadow-xl whitespace-nowrap pointer-events-none opacity-0 translate-x-1 group-hover:opacity-100 group-hover:translate-x-0 group-focus-visible:opacity-100 group-focus-visible:translate-x-0 transition-all duration-200 motion-reduce:transition-none z-50">
                {label}
              </span>
            </>
          );

          return (
            <div key={label} className="flex flex-col items-center gap-3.5">
              {href ? (
                <Link href={href} onClick={() => setActiveItem("")} aria-label={label} aria-current={active ? "page" : undefined} className={className}>
                  {content}
                </Link>
              ) : (
                <button type="button" aria-label={label} aria-pressed={active} onClick={() => setActiveItem(label)} className={className}>
                  {content}
                </button>
              )}
              {index === 0 && <span aria-hidden="true" className="h-px w-5 bg-white/10" />}
            </div>
          );
        })}
      </nav>
      <div className="flex flex-col items-center gap-3 w-full">
        <button
          type="button"
          onClick={() => setActiveItem("book")}
          title="Documentation"
          aria-label="Documentation"
          className={`relative group flex items-center justify-center w-8 h-8 rounded-full transition-all duration-200 ${
            activeItem === "book" ? "text-white" : "text-zinc-400 hover:text-white"
          }`}
        >
          <BookOpen strokeWidth={2.2} className="w-[17px] h-[17px]" />
          <span className="absolute left-[54px] px-2.5 py-1 text-xs font-medium text-white bg-black/90 rounded-md border border-white/10 shadow-xl whitespace-nowrap pointer-events-none opacity-0 group-hover:opacity-100 transition-opacity z-50">
            Documentation
          </span>
        </button>
        <button
          type="button"
          onClick={() => setActiveItem("store")}
          title="Plugin Store"
          aria-label="Marketplace"
          className={`relative group flex items-center justify-center w-8 h-8 rounded-full transition-all duration-200 ${
            activeItem === "store" ? "text-white" : "text-zinc-400 hover:text-white"
          }`}
        >
          <ShoppingBag strokeWidth={2.2} className="w-[17px] h-[17px]" />
          <span className="absolute left-[54px] px-2.5 py-1 text-xs font-medium text-white bg-black/90 rounded-md border border-white/10 shadow-xl whitespace-nowrap pointer-events-none opacity-0 group-hover:opacity-100 transition-opacity z-50">
            Marketplace
          </span>
        </button>
        <button
          type="button"
          onClick={() => setActiveItem("gradient")}
          title="AI Studio & Settings"
          aria-label="AI Engine"
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
