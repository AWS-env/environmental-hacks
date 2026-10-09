/**
 * taxonomy-sync.mts
 *
 * Bootstraps and syncs the Software Compute-Waste Taxonomy into GitHub.
 * Dependency-free: runs on Node 22.6+/23+/24 with native TypeScript stripping.
 *
 *   node scripts/taxonomy-sync.mts setup            # dry-run: labels + CODEOWNERS plan
 *   node scripts/taxonomy-sync.mts setup  --apply   # create labels + write CODEOWNERS
 *   node scripts/taxonomy-sync.mts sync             # dry-run: epics + checks plan
 *   node scripts/taxonomy-sync.mts sync   --apply   # create/update epics + checks
 *
 * Flags:
 *   --apply           actually write (default is dry-run)
 *   --limit N         only process the first N checks
 *   --only KEY        only process one taxonomy key (e.g. CODE-C1.1)
 *   --epics-only      only process the 4 owner epics
 *   --delay MS        pause between writes (default 1100ms; GitHub secondary limits)
 *
 * Auth:  GITHUB_TOKEN (classic PAT: repo  | fine-grained: Issues RW + Metadata R)
 * Repo:  from docs/taxonomy/owners.json (repo field), overridable with --repo owner/name
 */

import { readFileSync, writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const TAX = join(ROOT, "docs", "taxonomy");
const API = "https://api.github.com";

const argv = process.argv.slice(2);
const cmd = argv[0];
const flag = (name: string) => argv.includes(name);
const opt = (name: string, fallback = "") => {
  const i = argv.indexOf(name);
  return i >= 0 && argv[i + 1] ? argv[i + 1] : fallback;
};

const APPLY = flag("--apply");
const DRY = !APPLY;
const DELAY = Number(opt("--delay", "500"));
const LIMIT = Number(opt("--limit", "0")) || 0;
const ONLY = opt("--only", "");
const EPICS_ONLY = flag("--epics-only");
const NO_SUB = flag("--no-subissues");

const load = (p: string) => JSON.parse(readFileSync(p, "utf8"));
const owners = load(join(TAX, "owners.json"));
const checks: any[] = load(join(TAX, "checks.json"));
const mapping = load(join(TAX, "mapping.json"));
const REPO: string = opt("--repo", owners.repo);
const TOKEN = process.env.GITHUB_TOKEN || process.env.GH_TOKEN || "";

const OWNER_GROUP: Record<string, string> = {
  A: "In-process code efficiency",
  B: "Data & external I/O",
  C: "Language, client & build",
  D: "Runtime ops & AI",
};

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

async function api(method: string, path: string, body?: unknown) {
  const res = await fetch(`${API}${path}`, {
    method,
    headers: {
      Accept: "application/vnd.github+json",
      "X-GitHub-Api-Version": "2022-11-28",
      Authorization: `Bearer ${TOKEN}`,
      "User-Agent": "taxonomy-sync",
      ...(body ? { "Content-Type": "application/json" } : {}),
    },
    body: body ? JSON.stringify(body) : undefined,
  });
  const text = await res.text();
  const json = text ? JSON.parse(text) : {};
  if (!res.ok) {
    const err: any = new Error(`${method} ${path} -> ${res.status} ${json.message ?? ""}`);
    err.status = res.status;
    err.retryAfter = Number(res.headers.get("retry-after") || 0);
    throw err;
  }
  return json;
}

async function withRetry<T>(fn: () => Promise<T>, label: string): Promise<T> {
  for (let attempt = 1; attempt <= 5; attempt++) {
    try {
      return await fn();
    } catch (e: any) {
      const retryable = e.status === 403 || e.status === 429 || e.status === 502 || e.status === 503;
      if (!retryable || attempt === 5) throw e;
      const wait = (e.retryAfter || attempt * 5) * 1000;
      console.warn(`  ! ${label}: ${e.message} - retrying in ${wait / 1000}s`);
      await sleep(wait);
    }
  }
  throw new Error("unreachable");
}

// ---------------------------------------------------------------- setup

const LABELS: Array<{ name: string; color: string; description: string }> = [
  { name: "type: check", color: "0E8A16", description: "A taxonomy detector/check" },
  { name: "type: feature", color: "1D76DB", description: "New capability / epic" },
  { name: "status: ready", color: "0E8A16", description: "Scoped, ready to pick up" },
  { name: "status: in-progress", color: "FBCA04", description: "Someone is on it" },
  { name: "status: blocked", color: "B60205", description: "Waiting on something" },
  { name: "owner:A", color: "5319E7", description: "Owner A - in-process code efficiency" },
  { name: "owner:B", color: "5319E7", description: "Owner B - data & external I/O" },
  { name: "owner:C", color: "5319E7", description: "Owner C - language, client & build" },
  { name: "owner:D", color: "5319E7", description: "Owner D - runtime ops & AI" },
  { name: "layer:code", color: "C5DEF5", description: "Taxonomy layer: code" },
  { name: "layer:database", color: "C5DEF5", description: "Taxonomy layer: database" },
  { name: "layer:network", color: "C5DEF5", description: "Taxonomy layer: network/API" },
  { name: "layer:jobs", color: "C5DEF5", description: "Taxonomy layer: background jobs" },
  { name: "layer:frontend", color: "C5DEF5", description: "Taxonomy layer: frontend" },
  { name: "layer:ci", color: "C5DEF5", description: "Taxonomy layer: CI/CD" },
  { name: "layer:test", color: "C5DEF5", description: "Taxonomy layer: test code" },
  { name: "layer:observability", color: "C5DEF5", description: "Taxonomy layer: observability" },
  { name: "layer:llm", color: "C5DEF5", description: "Taxonomy layer: LLM/agentic" },
  { name: "layer:infrastructure", color: "C5DEF5", description: "Taxonomy layer: infrastructure" },
];

function buildCodeowners(): string {
  const team = `@${owners.team}`;
  const o = owners.owners;
  // NOTE: one pattern per line (CODEOWNERS format).
  return [
    "# Generated by scripts/taxonomy-sync.mts setup from docs/taxonomy/owners.json.",
    "# Do not edit by hand; change docs/taxonomy/owners.json and re-run `setup --apply`.",
    "",
    `*                       ${team}`,
    "",
    `/.github/               ${team}`,
    `/docs/                  ${team}`,
    `/scripts/               ${team}`,
    "",
    "# owner namespaces (swap handles in docs/taxonomy/owners.json)",
    `/detectors/owner-a/    @${o.A.handle}`,
    `/cdk/owner-a/          @${o.A.handle}`,
    `/detectors/owner-b/    @${o.B.handle}`,
    `/cdk/owner-b/          @${o.B.handle}`,
    `/detectors/owner-c/    @${o.C.handle}`,
    `/cdk/owner-c/          @${o.C.handle}`,
    `/detectors/owner-d/    @${o.D.handle}`,
    `/cdk/owner-d/          @${o.D.handle}`,
    "",
    "# shared hub - Owner D",
    `/shared/               @${o.D.handle}`,
    `/dashboard/            @${o.D.handle}`,
    `/cdk/hub/              @${o.D.handle}`,
    "",
  ].join("\n");
}

async function setup() {
  console.log(`\nSETUP (${DRY ? "dry-run" : "APPLY"})  repo=${REPO}  team=@${owners.team}\n`);
  console.log("Labels to ensure:");
  for (const l of LABELS) console.log(`  + ${l.name}`);
  const ruleNames = [...new Set(checks.map((c) => (c.rule ? `rule:${c.rule}` : "")).filter(Boolean))];
  console.log(`  + ${ruleNames.length} rule:* labels (${ruleNames.slice(0, 6).join(", ")}...)`);

  const codeowners = buildCodeowners();
  console.log("\n.github/CODEOWNERS (generated):");
  console.log(codeowners.replace(/^/gm, "  | "));

  if (DRY) return console.log("\n(dry-run) nothing written. Re-run with --apply.");

  let ok = 0;
  for (const l of [...LABELS, ...ruleNames.map((n) => ({ name: n, color: "EDEDED", description: "Taxonomy rule" }))]) {
    try {
      await withRetry(
        () => api("POST", `/repos/${REPO}/labels`, { name: l.name, color: l.color, description: l.description }),
        `label ${l.name}`,
      );
      ok++;
    } catch (e: any) {
      if (e.status === 422) { ok++; continue; } // already exists
      console.warn(`  ! label ${l.name}: ${e.message}`);
    }
    await sleep(Math.min(DELAY, 400));
  }
  writeFileSync(join(ROOT, ".github", "CODEOWNERS"), codeowners);
  console.log(`\nApplied ${ok} labels; wrote .github/CODEOWNERS.`);
}

// ---------------------------------------------------------------- sync

function checkBody(c: any): string {
  const skip = (v: string) => (v && v.trim() ? v : "_(none)_");
  return [
    `## Check \`${c.key}\``,
    "",
    `**Owner:** ${c.owner} · **Layer:** ${c.layer} · **Rule:** ${c.rule || "-"} · **Detection method:** ${c.detection_method || "-"}`,
    "",
    "### Wasteful behavior",
    skip(c.wasteful_behavior),
    "",
    "### When it is NOT necessarily wasteful",
    skip(c.not_wasteful_when),
    "",
    `### Affected resource\n${skip(c.affected_resource)}`,
    "",
    "### AWS mapping",
    `- **Detector:** ${skip(c.aws_detector)}`,
    `- **Run location:** ${skip(c.aws_run_location)}`,
    `- **Needs IAM role:** ${skip(c.needs_iam_role)}`,
    `- **Needs discussion:** ${skip(c.needs_discussion)}`,
    `- **Mapping confidence:** ${skip(c.mapping_confidence)}`,
    "",
    "### Detection spec (owner to fill)",
    "- **Detection signal:**",
    "- **Detection tool:**",
    "- **Telemetry needed:**",
    "- **Report output field:**",
    "- **False-positive risk:**",
    "- **Detectable (H/M/L):**",
    "- **Measurable (H/M/L):**",
    "",
    "### Evidence",
    `- **Source:** ${skip(c.source)}`,
    `- **Evidence type:** ${skip(c.evidence_type)}`,
    `- **Confidence:** ${skip(c.confidence)}`,
    `- **Notes:** ${skip(c.notes)}`,
    "",
    "### Acceptance criteria",
    `- [ ] Detector implemented under \`detectors/owner-${c.owner.toLowerCase()}/\``,
    "- [ ] Emits an evidence-backed finding",
    "- [ ] Unit test for the detector",
    "- [ ] Detection spec fields above filled",
    "",
    "---",
    `_Taxonomy key: \`${c.key}\` · content hash: \`${c.content_hash}\` · source: docs/taxonomy/checks.yaml_`,
  ].join("\n");
}

function epicBody(letter: string): string {
  const group = OWNER_GROUP[letter];
  const mine = checks.filter((c) => c.owner === letter);
  const byLayer: Record<string, number> = {};
  for (const c of mine) byLayer[c.layer_label] = (byLayer[c.layer_label] || 0) + 1;
  return [
    `## Owner ${letter} — ${group}`,
    "",
    `Parent epic for **${mine.length}** taxonomy checks. Sub-issues list every check.`,
    "",
    "### Layers in scope",
    ...Object.entries(byLayer).map(([k, v]) => `- \`${k}\`: ${v}`),
    "",
    "### Workflow",
    "Branch `owner-" + letter.toLowerCase() + "/<issue>-<type>-<slug>` → PR `Closes #<issue>`.",
    "",
    `_Generated by scripts/taxonomy-sync.mts. Owner handle: @${owners.owners[letter].handle} (placeholder)._`,
  ].join("\n");
}

// Fetch every issue once and index by title. Used to adopt issues that already
// exist (e.g. created by a previous run after the last mapping checkpoint), so
// re-running is always safe and never duplicates.
async function listIssuesByTitle(): Promise<Map<string, any>> {
  const map = new Map<string, any>();
  for (let page = 1; ; page++) {
    const arr = await withRetry(
      () => api("GET", `/repos/${REPO}/issues?state=all&per_page=100&page=${page}`),
      `list issues p${page}`,
    );
    for (const i of arr) if (!i.pull_request) map.set(i.title, i);
    if (arr.length < 100) break;
  }
  return map;
}

async function sync() {
  console.log(`\nSYNC (${DRY ? "dry-run" : "APPLY"})  repo=${REPO}`);
  if (APPLY && !TOKEN) throw new Error("GITHUB_TOKEN not set - required for --apply");

  // adopt existing issues by title (safe resume)
  let existing = new Map<string, any>();
  if (TOKEN) {
    existing = await listIssuesByTitle();
    console.log(`\nFound ${existing.size} existing issues (adopt-by-title enabled).`);
  }

  // 1. epics
  console.log("\nEpics:");
  const epicNumbers: Record<string, number> = {};
  for (const letter of ["A", "B", "C", "D"]) {
    const title = mapping.epics[letter].title;
    if (mapping.epics[letter].number) {
      epicNumbers[letter] = mapping.epics[letter].number;
      console.log(`  [${letter}] ${title}  -> #${epicNumbers[letter]}`);
      continue;
    }
    const found = existing.get(title);
    if (found) {
      mapping.epics[letter].number = found.number;
      mapping.epics[letter].node_id = found.node_id;
      epicNumbers[letter] = found.number;
      console.log(`  [${letter}] ${title}  -> adopted #${found.number}`);
      continue;
    }
    console.log(`  [${letter}] ${title}  -> (create)`);
    if (DRY) continue;
    const issue = await withRetry(
      () => api("POST", `/repos/${REPO}/issues`, {
        title, body: epicBody(letter),
        labels: ["type: feature", "status: ready", owners.owners[letter].label],
      }),
      title,
    );
    mapping.epics[letter].number = issue.number;
    mapping.epics[letter].node_id = issue.node_id;
    epicNumbers[letter] = issue.number;
    await sleep(DELAY);
  }

  // 2. checks
  let list = checks;
  if (ONLY) list = list.filter((c) => c.key === ONLY);
  if (LIMIT) list = list.slice(0, LIMIT);
  console.log(`\nChecks: ${list.length}${ONLY || LIMIT ? ` (filtered from ${checks.length})` : ""}`);

  let created = 0, updated = 0, skipped = 0, adopted = 0;
  for (const c of list) {
    const m = mapping.checks[c.key];
    const labels = ["type: check", "status: ready", `owner:${c.owner}`, `layer:${c.layer_label}`];
    if (c.rule) labels.push(`rule:${c.rule}`);
    const assignees = owners.owners[c.owner] ? [owners.owners[c.owner].handle] : [];

    if (m.number) {
      if (m.hash === c.content_hash) { skipped++; continue; }
      console.log(`  ~ #${m.number} ${c.key} (hash ${m.hash}->${c.content_hash})`);
      if (!DRY) {
        await withRetry(() => api("PATCH", `/repos/${REPO}/issues/${m.number}`, {
          title: m.title, body: checkBody(c), labels, assignees,
        }), c.key);
        m.hash = c.content_hash;
        updated++;
        await sleep(DELAY);
      }
      continue;
    }

    // adopt an existing issue with the same title (resume / recovered mapping)
    if (!m.number) {
      const found = existing.get(m.title);
      if (found) {
        m.number = found.number;
        m.node_id = found.node_id;
        m.id = found.id;
        m.hash = c.content_hash;
        adopted++;
        if (epicNumbers[c.owner] && !NO_SUB) {
          try { await api("POST", `/repos/${REPO}/issues/${epicNumbers[c.owner]}/sub_issues`, { sub_issue_id: found.id }); } catch {}
        }
        if (adopted % 50 === 0) console.log(`    ...adopted ${adopted}`);
        continue;
      }
    }

    console.log(`  + ${c.key}  ${m.title}`);
    if (DRY) continue;
    const issue = await withRetry(
      () => api("POST", `/repos/${REPO}/issues`, { title: m.title, body: checkBody(c), labels, assignees }),
      c.key,
    );
    m.number = issue.number;
    m.node_id = issue.node_id;
    m.id = issue.id;
    m.hash = c.content_hash;
    created++;
    // attach as sub-issue of the owner epic (best effort)
    if (epicNumbers[c.owner] && !NO_SUB) {
      try {
        await api("POST", `/repos/${REPO}/issues/${epicNumbers[c.owner]}/sub_issues`, { sub_issue_id: issue.id });
      } catch (e: any) { /* sub-issues optional; ignore */ }
    }
    await sleep(DELAY);
    if (created % 25 === 0) {
      writeFileSync(join(TAX, "mapping.json"), JSON.stringify(mapping, null, 2) + "\n");
      console.log(`    ...checkpoint saved (${created} created)`);
    }
  }

  console.log(`\nSummary: created=${created} adopted=${adopted} updated=${updated} unchanged=${skipped}`);
  if (DRY) return console.log("(dry-run) nothing written. Re-run with --apply.");

  mapping.generated_at = new Date().toISOString();
  writeFileSync(join(TAX, "mapping.json"), JSON.stringify(mapping, null, 2) + "\n");
  console.log("mapping.json updated.");
}

(async () => {
  try {
    if (cmd === "setup") await setup();
    else if (cmd === "sync") await sync();
    else {
      console.log("Usage: node scripts/taxonomy-sync.mts <setup|sync> [--apply] [--limit N] [--only KEY] [--epics-only]");
      process.exit(1);
    }
  } catch (e: any) {
    console.error(`\nERROR: ${e.message}`);
    process.exit(1);
  }
})();
