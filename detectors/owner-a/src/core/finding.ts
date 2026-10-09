import { createHash } from "node:crypto";

export type Severity = "high" | "medium" | "low" | "info";
export type Confidence = "high" | "medium" | "low";
export type CostTier = "heavy" | "light" | "unknown";

export interface FindingLocation {
  path: string;
  startLine: number;
  endLine: number;
}

export interface FindingEvidence {
  snippet: string;
  symbol?: string;
  module?: string;
  costTier?: CostTier;
}

export interface FindingImpact {
  quantified: false;
  reason: string;
}

export interface FindingReference {
  id: string;
  title: string;
  url: string;
}

export interface FindingDetector {
  id: "owner-a-static-scan";
  version: string;
}

export interface Finding {
  check: string;
  kind: string;
  fingerprint: string;
  /**
   * Line-free semantic anchor for the shared contract v1 fingerprint
   * (`sha256([repository_id, check_id, scope_id, identity])`). Optional so a
   * check without one still adapts: the adapter then uses kind + snippet.
   */
  identity?: string;
  location: FindingLocation;
  evidence: FindingEvidence;
  why: string;
  severity: Severity;
  confidence: Confidence;
  limitations: string[];
  evidenceTier: "static";
  impact: FindingImpact;
  references: FindingReference[];
  agentPrompt: string;
  detector: FindingDetector;
}

export interface DetectorContext {
  filePath: string;
  fileContent: string;
  options?: Record<string, unknown>;
}

export function generateFingerprint(
  check: string,
  kind: string,
  path: string,
  identifier: string
): string {
  return createHash("sha256")
    .update(`${check}:${kind}:${path}:${identifier}`)
    .digest("hex")
    .slice(0, 16);
}
