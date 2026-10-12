import { Suspense } from "react";
import type { Metadata } from "next";
import EnvironmentClient from "@/components/environment/EnvironmentClient";

export const metadata: Metadata = {
  title: "Environment · EcoAudit",
  description: "Live measured power, energy, CO₂e and water from run-it on your machine.",
};

// Reads ?port= and talks to run-it on localhost, so it renders on the client.
export default function EnvironmentPage() {
  return (
    <Suspense fallback={<div className="rp-page" />}>
      <EnvironmentClient />
    </Suspense>
  );
}
