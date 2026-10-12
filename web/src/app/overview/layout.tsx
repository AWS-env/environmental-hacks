import type { ReactNode } from "react";
import WorkspaceShell from "@/components/WorkspaceShell";

export default function OverviewLayout({ children }: { children: ReactNode }) {
  return <WorkspaceShell>{children}</WorkspaceShell>;
}
