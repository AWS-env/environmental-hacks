import { Suspense } from "react";
import type { Metadata } from "next";
import ReportsClient, { ReportsFallback } from "@/components/reports/ReportsClient";

export const metadata: Metadata = {
  title: "Report · EcoAudit",
  description: "Findings, coverage and provenance of a read-only compute-waste scan.",
};

// The report is chosen from the URL (?scan=, ?sample) and this browser's scan history, so it renders on the client.
export default function ReportsPage() {
  return (
    <Suspense fallback={<ReportsFallback />}>
      <ReportsClient />
    </Suspense>
  );
}
