import type { ReactNode } from "react";
import Sidebar from "@/components/Sidebar";

export default function WorkspaceShell({ children }: { children: ReactNode }) {
  // Always dark (the particle field is drawn for a night sky); data-theme keeps the sidebar and dark: variants in step.
  return (
    <div data-theme="dark" className="relative flex h-dvh w-full overflow-hidden bg-[#0a0d0e] font-sans text-zinc-100 selection:bg-white selection:text-black">
      <Sidebar />
      <div className="relative z-10 h-dvh min-w-0 flex-1 overflow-hidden pl-[72px]">
        {children}
      </div>
    </div>
  );
}
