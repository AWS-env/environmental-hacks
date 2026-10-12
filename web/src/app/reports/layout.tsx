import type { ReactNode } from "react";
import ParticleTerrain from "@/components/ParticleTerrain";
import WorkspaceShell from "@/components/WorkspaceShell";
import "@/components/panels.css";

/** The workspace shell (sidebar, always dark) over the particle backdrop; the report scrolls in its own column. */
export default function ReportsLayout({ children }: { children: ReactNode }) {
  return (
    <WorkspaceShell>
      <ParticleTerrain />
      {children}
    </WorkspaceShell>
  );
}
