/**
 * Shared detector contract v1 adapter for Owner A (docs/DETECTOR_CONTRACT.md).
 *
 * `evaluate(input)` takes a contract input payload, runs the requested owner-a
 * check over each scope's static Python sources and returns a contract result.
 * It never imports or executes the supplied source; it only parses it.
 */
import { createHash } from "node:crypto";
import { Finding } from "./core/finding.js";
import { parsePythonSource } from "./core/parse.js";
import { ARTIFACT_CHECKS, CHECKS, RegisteredCheck } from "./registry.js";
import { RegisteredArtifactCheck } from "./core/artifact.js";

export type ContractConfidence = "low" | "medium" | "high";
export type ContractStatus = "completed" | "partial" | "unavailable" | "error";

export interface ContractSource {
  source_id: string;
  scope_id: string;
  kind: "static" | "telemetry" | "artifact";
  locator: string;
  content?: string;
  data?: Record<string, unknown>;
}

interface ContractBase {
  schema_version: "1.0";
  repository_id: string;
  scan_id: string;
  commit_sha: string;
  check_id: string;
  detector_version: string;
  context: Record<string, unknown>;
  scope: string[];
}

export interface ContractInput extends ContractBase {
  kind: "input";
  sources: ContractSource[];
}

export interface ContractEvidence {
  source_id: string;
  kind: "static" | "artifact";
  locator: string;
  /** Static evidence: one-based first quoted line. */
  line_start?: number;
  /** Artifact evidence: the top-level `data` field being cited. */
  field?: string;
  /** Static: the exact quoted lines. Artifact: the cited field's value, in full. */
  value: unknown;
}

export interface ContractFinding {
  fingerprint: string;
  scope_id: string;
  identity: string;
  summary: string;
  confidence: ContractConfidence;
  recommendation: string;
  references: string[];
  evidence: ContractEvidence[];
}

export interface ContractResult extends ContractBase {
  kind: "result";
  status: ContractStatus;
  coverage: { evaluated_scope: string[]; limitations: string[] };
  findings: ContractFinding[];
  measurements: never[];
}

/** Thrown when the payload is not a contract v1 input (no result can be built). */
export class ContractInputError extends Error {}

const IDENTITY_FIELDS = [
  "schema_version",
  "repository_id",
  "scan_id",
  "commit_sha",
  "check_id",
  "detector_version",
  "context",
  "scope",
] as const;

const PYTHON_LOCATOR = /\.pyi?$/;
/** Separators Python's `str.splitlines()` honours but tree-sitter rows do not. */
const AMBIGUOUS_LINE_BREAKS = /\r(?!\n)|[\v\f\x1c\x1d\x1e\x85\u2028\u2029]/;
const MAX_EVIDENCE_LINES = 10;

const STATIC_ONLY_LIMITATION =
  "Static analysis only: findings prove the source pattern, not runtime cost or environmental impact; no measurements are reported.";

/**
 * Contract fingerprint: lowercase SHA-256 of the compact UTF-8 JSON array
 * `[repository_id, check_id, scope_id, identity]` (matches
 * `shared/contracts/validation.py:fingerprint`).
 */
export function contractFingerprint(
  repositoryId: string,
  checkId: string,
  scopeId: string,
  identity: string
): string {
  return createHash("sha256")
    .update(JSON.stringify([repositoryId, checkId, scopeId, identity]), "utf8")
    .digest("hex");
}

function assertInput(payload: unknown): asserts payload is ContractInput {
  const p = payload as Partial<ContractInput> | null;
  if (!p || typeof p !== "object") throw new ContractInputError("payload must be an object");
  if (p.schema_version !== "1.0") {
    throw new ContractInputError("unsupported schema_version; contract v1 is required");
  }
  if (p.kind !== "input") throw new ContractInputError("expected kind 'input'");
  for (const key of IDENTITY_FIELDS) {
    if (p[key] === undefined) throw new ContractInputError(`missing ${key}`);
  }
  if (!Array.isArray(p.scope) || p.scope.length === 0) {
    throw new ContractInputError("scope must be a nonempty array");
  }
  if (!Array.isArray(p.sources)) throw new ContractInputError("sources must be an array");
}

function resultFor(
  input: ContractInput,
  status: ContractStatus,
  evaluated: string[],
  limitations: string[],
  findings: ContractFinding[]
): ContractResult {
  const identity = Object.fromEntries(
    IDENTITY_FIELDS.map((key) => [key, input[key]])
  ) as unknown as ContractBase;
  return {
    ...identity,
    kind: "result",
    status,
    coverage: { evaluated_scope: evaluated, limitations },
    findings,
    measurements: [],
  };
}

/** Exact source lines `start..end` (1-based), capped, as the contract quotes them. */
function quoteLines(content: string, startLine: number, endLine: number): string | null {
  const lines = content.split(/\r?\n/);
  const last = Math.min(endLine, startLine + MAX_EVIDENCE_LINES - 1);
  const quoted = lines.slice(startLine - 1, last);
  if (quoted.length === 0 || quoted.join("").trim() === "") return null;
  return quoted.join("\n");
}

function toContractFinding(
  input: ContractInput,
  source: ContractSource,
  finding: Finding,
  usedIdentities: Map<string, number>
): ContractFinding | null {
  const value = quoteLines(
    source.content ?? "",
    finding.location.startLine,
    finding.location.endLine
  );
  if (value === null) return null;

  // Identity: the check's semantic anchor (or kind + snippet), qualified by the
  // locator when a scope holds several files, plus an ordinal for repeats so
  // contract fingerprints stay unique.
  const base = finding.identity ?? `${finding.kind}:${finding.evidence.snippet}`;
  const qualified = `${source.locator}::${base}`;
  const seen = usedIdentities.get(qualified) ?? 0;
  usedIdentities.set(qualified, seen + 1);
  const identity = seen === 0 ? qualified : `${qualified}#${seen + 1}`;

  const references = [
    ...new Set(finding.references.map((r) => r.url).filter((u) => /^https?:\/\/[^/\s]+(\/.*)?$/.test(u))),
  ];
  if (references.length === 0) return null;

  return {
    fingerprint: contractFingerprint(input.repository_id, input.check_id, source.scope_id, identity),
    scope_id: source.scope_id,
    identity,
    summary: finding.why,
    confidence: finding.confidence,
    recommendation: finding.agentPrompt,
    references,
    evidence: [
      {
        source_id: source.source_id,
        kind: "static",
        locator: source.locator,
        line_start: finding.location.startLine,
        value,
      },
    ],
  };
}

/** Evaluate one scope; returns findings, or a reason it could not be fully evaluated. */
function evaluateScope(
  input: ContractInput,
  scopeId: string,
  check: RegisteredCheck
): { findings: ContractFinding[] } | { reason: string } {
  const sources = input.sources.filter((s) => s.scope_id === scopeId);
  const staticSources = sources.filter((s) => s.kind === "static");
  if (staticSources.length === 0) {
    return { reason: `${scopeId}: no static source supplied; ${input.check_id} needs Python source text.` };
  }
  const unsupported = staticSources.filter((s) => !PYTHON_LOCATOR.test(s.locator));
  if (unsupported.length > 0) {
    return {
      reason: `${scopeId}: unsupported source ${unsupported.map((s) => s.locator).join(", ")}; owner-a checks support Python (.py/.pyi) only.`,
    };
  }

  const findings: ContractFinding[] = [];
  const usedIdentities = new Map<string, number>();
  for (const source of staticSources) {
    const content = source.content ?? "";
    if (AMBIGUOUS_LINE_BREAKS.test(content)) {
      return {
        reason: `${scopeId}: ${source.locator} uses non-LF line separators, so evidence lines cannot be cited exactly.`,
      };
    }
    const parsed = parsePythonSource(source.locator, content);
    if (parsed.hasSyntaxError) {
      return { reason: `${scopeId}: ${source.locator} could not be parsed (syntax error); not evaluated.` };
    }
    for (const finding of check.run(parsed)) {
      const mapped = toContractFinding(input, source, finding, usedIdentities);
      if (!mapped) {
        return { reason: `${scopeId}: a ${input.check_id} finding in ${source.locator} could not be cited; scope not certified.` };
      }
      findings.push(mapped);
    }
  }
  return { findings };
}

const ARTIFACT_ONLY_LIMITATION =
  "Artifact analysis only: findings rest on a client-produced artifact from one run; no code was executed by this detector and no environmental values are reported.";

/** Evaluate one scope of an artifact check: exactly one artifact source with a `data` object is required. */
function evaluateArtifactScope(
  input: ContractInput,
  scopeId: string,
  check: RegisteredArtifactCheck
): { findings: ContractFinding[] } | { reason: string } {
  const sources = input.sources.filter((s) => s.scope_id === scopeId);
  const artifacts = sources.filter((s) => s.kind === "artifact");
  if (artifacts.length !== 1) {
    return {
      reason: `${scopeId}: expected exactly one ${check.artifactLabel} artifact source, got ${artifacts.length}; ${input.check_id} needs it as evidence.`,
    };
  }
  const source = artifacts[0];
  if (!source.data || typeof source.data !== "object" || Array.isArray(source.data)) {
    return { reason: `${scopeId}: ${source.locator} has no normalized data object; not evaluated.` };
  }
  const outcome = check.run(source.data, input.context);
  if (outcome.kind === "unavailable") {
    return { reason: `${scopeId}: ${source.locator}: ${outcome.reason}` };
  }

  const findings: ContractFinding[] = [];
  const usedIdentities = new Map<string, number>();
  for (const f of outcome.findings) {
    const missing = f.fields.filter((field) => !(field in (source.data as Record<string, unknown>)));
    if (missing.length > 0 || f.references.length === 0) {
      return { reason: `${scopeId}: a ${input.check_id} finding in ${source.locator} could not be cited; scope not certified.` };
    }
    const qualified = `${source.locator}::${f.identity}`;
    const seen = usedIdentities.get(qualified) ?? 0;
    usedIdentities.set(qualified, seen + 1);
    const identity = seen === 0 ? qualified : `${qualified}#${seen + 1}`;
    findings.push({
      fingerprint: contractFingerprint(input.repository_id, input.check_id, source.scope_id, identity),
      scope_id: source.scope_id,
      identity,
      summary: f.summary,
      confidence: f.confidence,
      recommendation: f.recommendation,
      references: [...new Set(f.references)],
      evidence: f.fields.map((field) => ({
        source_id: source.source_id,
        kind: "artifact" as const,
        locator: source.locator,
        field,
        value: (source.data as Record<string, unknown>)[field],
      })),
    });
  }
  return { findings };
}

/**
 * Evaluate a contract v1 input with the requested owner-a check.
 * Throws `ContractInputError` only when the payload is not a contract input at
 * all; every other problem becomes an honest `partial` / `unavailable` / `error`
 * result, never a `completed` result without evaluation.
 */
export function evaluate(
  payload: unknown,
  checks: ReadonlyMap<string, RegisteredCheck> = CHECKS,
  artifactChecks: ReadonlyMap<string, RegisteredArtifactCheck> = ARTIFACT_CHECKS
): ContractResult {
  assertInput(payload);
  const input = payload;

  const check = checks.get(input.check_id);
  const artifactCheck = check ? undefined : artifactChecks.get(input.check_id);
  if (artifactCheck) return evaluateArtifactCheck(input, artifactCheck);
  if (!check) {
    return resultFor(input, "unavailable", [], [
      `${input.check_id} is not implemented by the owner-a detector (supported: ${[...checks.keys(), ...artifactChecks.keys()].join(", ")}).`,
    ], []);
  }
  if (input.detector_version !== check.version) {
    return resultFor(input, "unavailable", [], [
      `detector_version ${input.detector_version} requested; this build implements ${input.check_id} ${check.version}.`,
    ], []);
  }

  const evaluated: string[] = [];
  const limitations = [STATIC_ONLY_LIMITATION, ...check.limitations];
  const findings: ContractFinding[] = [];
  try {
    for (const scopeId of input.scope) {
      const outcome = evaluateScope(input, scopeId, check);
      if ("reason" in outcome) {
        limitations.push(outcome.reason);
      } else {
        evaluated.push(scopeId);
        findings.push(...outcome.findings);
      }
    }
  } catch (error) {
    return resultFor(input, "error", [], [
      `${input.check_id} evaluation failed: ${error instanceof Error ? error.message : String(error)}`,
    ], []);
  }

  const status: ContractStatus =
    evaluated.length === input.scope.length
      ? "completed"
      : evaluated.length > 0
        ? "partial"
        : "unavailable";
  return resultFor(input, status, evaluated, limitations, findings);
}

function evaluateArtifactCheck(input: ContractInput, check: RegisteredArtifactCheck): ContractResult {
  if (input.detector_version !== check.version) {
    return resultFor(input, "unavailable", [], [
      `detector_version ${input.detector_version} requested; this build implements ${input.check_id} ${check.version}.`,
    ], []);
  }
  const evaluated: string[] = [];
  const limitations = [ARTIFACT_ONLY_LIMITATION, ...check.limitations];
  const findings: ContractFinding[] = [];
  try {
    for (const scopeId of input.scope) {
      const outcome = evaluateArtifactScope(input, scopeId, check);
      if ("reason" in outcome) {
        limitations.push(outcome.reason);
      } else {
        evaluated.push(scopeId);
        findings.push(...outcome.findings);
      }
    }
  } catch (error) {
    return resultFor(input, "error", [], [
      `${input.check_id} evaluation failed: ${error instanceof Error ? error.message : String(error)}`,
    ], []);
  }
  const status: ContractStatus =
    evaluated.length === input.scope.length ? "completed" : evaluated.length > 0 ? "partial" : "unavailable";
  return resultFor(input, status, evaluated, limitations, findings);
}
