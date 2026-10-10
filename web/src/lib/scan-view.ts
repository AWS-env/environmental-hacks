/** Normalises a scan report (primary) or a findings-hub scan (fallback) into one shape for the results view. */
import type { CheckStatus, Confidence, Evidence, HubScan, ScanReport } from "./scan-api";

export interface FindingView {
  id: string;
  summary: string;
  confidence: Confidence;
  recommendation: string;
  references: string[];
  file: string | null;
  line: number | null;
  evidence: Evidence[];
}

export interface CheckView {
  checkId: string;
  owner: string | null;
  pattern: string | null;
  layer: string | null;
  status: CheckStatus;
  reason: string | null;
  scope: number;
  evaluated: number;
  limitations: string[];
  limitationsTotal: number;
  findings: FindingView[];
}

const STATUS_ORDER: Record<CheckStatus, number> = { error: 0, partial: 1, completed: 2, unavailable: 3, not_applicable: 4 };
const CONFIDENCE_ORDER: Record<Confidence, number> = { high: 0, medium: 1, low: 2 };

/** Same rule as scanner/report.py `_primary`: first static evidence gives file and line. */
export function primaryLocation(evidence: Evidence[]): { file: string | null; line: number | null } {
  const item = evidence.find((e) => e.kind === "static") ?? evidence[0];
  if (!item) return { file: null, line: null };
  return { file: item.locator, line: item.kind === "static" ? item.line_start ?? null : null };
}

function sortChecks(checks: CheckView[]): CheckView[] {
  for (const check of checks) {
    check.findings.sort((a, b) => CONFIDENCE_ORDER[a.confidence] - CONFIDENCE_ORDER[b.confidence]
      || (a.file ?? "").localeCompare(b.file ?? "") || (a.line ?? 0) - (b.line ?? 0));
  }
  // Checks with findings first, strongest confidence first; then by status so errors surface.
  const best = (c: CheckView) => (c.findings.length ? CONFIDENCE_ORDER[c.findings[0].confidence] : 3);
  return checks.sort((a, b) => best(a) - best(b) || b.findings.length - a.findings.length
    || STATUS_ORDER[a.status] - STATUS_ORDER[b.status] || a.checkId.localeCompare(b.checkId));
}

export function checksFromReport(report: ScanReport): CheckView[] {
  const byCheck = new Map<string, FindingView[]>();
  for (const f of report.findings) {
    const list = byCheck.get(f.check_id) ?? [];
    list.push({
      id: f.id, summary: f.summary, confidence: f.confidence, recommendation: f.recommendation,
      references: f.references, file: f.file, line: f.line, evidence: f.evidence,
    });
    byCheck.set(f.check_id, list);
  }
  return sortChecks(report.checks.map((c) => ({
    checkId: c.check_id, owner: c.owner, pattern: c.pattern, layer: c.layer, status: c.status, reason: c.reason,
    scope: c.scope_size, evaluated: c.evaluated_size, limitations: c.limitations,
    limitationsTotal: c.limitations_total, findings: byCheck.get(c.check_id) ?? [],
  })));
}

/** Hub results carry only detector-validated checks, and no taxonomy metadata (pattern, layer). */
export function checksFromHub(hub: HubScan): CheckView[] {
  const byCheck = new Map<string, FindingView[]>();
  for (const f of hub.findings) {
    const list = byCheck.get(f.check_id) ?? [];
    list.push({
      id: f.fingerprint, summary: f.summary, confidence: f.confidence, recommendation: f.recommendation,
      references: f.references, ...primaryLocation(f.evidence), evidence: f.evidence,
    });
    byCheck.set(f.check_id, list);
  }
  return sortChecks(hub.results.map((r) => ({
    checkId: r.check_id, owner: /^owner-([a-z])\./.exec(r.source)?.[1].toUpperCase() ?? null, pattern: null,
    layer: null, status: r.status, reason: null, scope: r.scope_count, evaluated: r.evaluated_count,
    limitations: r.limitations ?? [], limitationsTotal: r.limitations?.length ?? 0, findings: byCheck.get(r.check_id) ?? [],
  })));
}

/** A link to the cited line at the scanned commit, only when the report says the SHA is a real Git commit. */
export function sourceLink(report: ScanReport, file: string | null, line: number | null): string | null {
  const { url, commit_sha: sha, commit_source: source } = report.repository;
  if (!file || source === "content-hash" || !/^[0-9a-f]{40}$/.test(sha) || !url.startsWith("https://github.com/")) return null;
  const path = file.split("/").map(encodeURIComponent).join("/");
  return `${url}/blob/${sha}/${path}${line ? `#L${line}` : ""}`;
}
