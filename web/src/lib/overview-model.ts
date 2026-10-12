/** Illustrative workspace data only. Never used by the real scan/report clients. */
export const workspaces = ["storefront-api", "payments-worker", "customer-portal"];
export const categories = ["Code", "Database", "Network", "Jobs", "CI", "Frontend", "Cloud", "Observability"];
export const exampleFindings = [
  { check: "DB-34", title: "Query constructed before cache lookup", location: "src/catalog.php:48", evidence: "Source", confidence: "High", state: "Static pattern" },
  { check: "CODE-C3.2", title: "Loop-invariant computation", location: "src/pricing.py:72", evidence: "Source", confidence: "High", state: "Static pattern" },
  { check: "JOB-04", title: "Schedules share a capacity window", location: "config/schedules.json:16", evidence: "Inventory", confidence: "Medium", state: "Candidate" },
  { check: "NET-07", title: "Response compression needs verification", location: "GET /catalog", evidence: "HTTP capture needed", confidence: "—", state: "Awaiting evidence" },
  { check: "DB-16", title: "Query execution needs runtime review", location: "src/search.php:91", evidence: "Query events needed", confidence: "—", state: "Awaiting evidence" },
];

export function sampleAudit(frame: number, workspace: number) {
  const findings = Math.round(24 + workspace * 4 + Math.sin(frame * .12 + workspace) * 5 + Math.sin(frame * .31) * 2);
  const completed = Math.round(82 + Math.sin(frame * .07 + workspace) * 4);
  const partial = 12;
  const unavailable = 114 - completed - partial;
  return { findings, candidates: 8 + workspace, completed, partial, unavailable, notApplicable: 6, errors: 0,
    coverage: Math.round(completed / 114 * 1000) / 10,
    duration: Math.round((18 + workspace * 3 + Math.sin(frame * .14) * 4) * 10) / 10 };
}
