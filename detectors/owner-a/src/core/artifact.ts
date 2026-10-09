/**
 * Types for owner-a checks whose evidence is a client-produced artifact (for example a
 * `memray stats --json` export) instead of Python source text. The contract adapter
 * (`src/contract.ts`) hands such a check the artifact's normalized `data` object and turns
 * its findings into contract evidence that cites top-level `data` fields.
 */
export type ArtifactConfidence = "low" | "medium" | "high";

export interface ArtifactFinding {
  /** Line-free semantic anchor; the adapter qualifies it with the artifact locator. */
  identity: string;
  summary: string;
  confidence: ArtifactConfidence;
  recommendation: string;
  /** HTTP(S) technical references. */
  references: string[];
  /** Top-level `data` field names this finding rests on; the adapter quotes each value in full. */
  fields: string[];
}

export type ArtifactCheckOutcome =
  | { kind: "evaluated"; findings: ArtifactFinding[] }
  /** The artifact lacks what the check needs: nothing is certified (never reported as clean). */
  | { kind: "unavailable"; reason: string };

export interface RegisteredArtifactCheck {
  version: string;
  /** Short label used in limitation text, e.g. "memray stats JSON". */
  artifactLabel: string;
  /** Check-wide coverage limitations, stated on every result for this check. */
  limitations: string[];
  /** Pure: reads only `data` and `context`, never touches the client's code or environment. */
  run: (data: Record<string, unknown>, context: Record<string, unknown>) => ArtifactCheckOutcome;
}
