import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { checkCodeC61, C61_DEFAULTS } from "../src/checks/code-c6-1/index.js";
import { evaluate } from "../src/contract.js";
import { validatePair, validatorAvailable } from "./helpers/contract.js";

const FIX = join(__dirname, "fixtures", "code-c6-1");
// Real `memray stats --json` exports (memray 1.x, Python 3.13) of wasteful.py and steady.py in this folder.
const churn = JSON.parse(readFileSync(join(FIX, "memray-stats-churn.json"), "utf-8")) as Record<string, unknown>;
const steady = JSON.parse(readFileSync(join(FIX, "memray-stats-steady.json"), "utf-8")) as Record<string, unknown>;

function input(data: Record<string, unknown>, context: Record<string, unknown> = {}, name = "memray-stats.json") {
  return {
    schema_version: "1.0" as const,
    kind: "input" as const,
    repository_id: "github:AWS-env/environmental-hacks-fixtures",
    scan_id: "owner-a-fixture-scan",
    commit_sha: "0123456789abcdef0123456789abcdef01234567",
    check_id: "CODE-C6.1",
    detector_version: "0.1.0",
    context: { language: "python", ...context },
    scope: [`artifact:${name}`],
    sources: [{ source_id: name, scope_id: `artifact:${name}`, kind: "artifact" as const, locator: name, data }],
  };
}

describe("CODE-C6.1 Unnecessary object (memray stats artifact)", () => {
  describe("check on real exports", () => {
    it("flags the two hot allocation sites of the churn run, with the cited numbers", () => {
      const out = checkCodeC61(churn);
      expect(out.kind).toBe("evaluated");
      if (out.kind !== "evaluated") return;
      expect(out.findings.map((f) => f.identity)).toEqual([
        "allocation-churn:score:wasteful.py#1",
        "allocation-churn:score:wasteful.py#2",
      ]);
      expect(out.findings[0].summary).toContain("score:wasteful.py:7");
      expect(out.findings[0].summary).toContain("21000 allocations");
      expect(out.findings[0].summary).toMatch(/327x/);
      // decimal units, same numbers memray prints (27.424MB total, 83.868kB peak)
      expect(out.findings[0].summary).toContain("27.4 MB in total against a 83.9 kB peak");
      expect(out.findings[0].confidence).toBe("medium"); // 87% of allocations, churn 327x
      expect(out.findings[1].confidence).toBe("low"); // 12% share
      expect(out.findings[0].fields).toEqual(["top_allocations_by_count", "total_bytes_allocated", "metadata"]);
    });

    it("reports nothing for the steady run (churn about 1.1x): evaluated, not unavailable", () => {
      expect(checkCodeC61(steady)).toEqual({ kind: "evaluated", findings: [] });
    });

    it("ignores locations in the standard library and frozen modules", () => {
      const data = structuredClone(churn) as any;
      data.top_allocations_by_count = [
        { location: "_get_code_from_file:<frozen runpy>:266", count: 50000 },
        { location: "namedtuple:/usr/local/lib/python3.13/collections/__init__.py:444", count: 50000 },
        { location: "run:/app/.venv/lib/python3.13/site-packages/x/y.py:9", count: 50000 },
      ];
      data.total_num_allocations = 150000;
      expect(checkCodeC61(data)).toEqual({ kind: "evaluated", findings: [] });
    });
  });

  describe("thresholds (context overrides)", () => {
    it("boundary: churn just below the minimum reports nothing, at the minimum reports", () => {
      const data = structuredClone(churn) as any;
      const ratio = data.total_bytes_allocated / data.metadata.peak_memory; // 327.0
      expect(checkCodeC61(data, { churn_ratio_min: ratio + 1 })).toEqual({ kind: "evaluated", findings: [] });
      const at = checkCodeC61(data, { churn_ratio_min: ratio });
      expect(at.kind === "evaluated" && at.findings.length).toBe(2);
    });

    it("a run that allocated under min_total_bytes is too small to judge", () => {
      expect(checkCodeC61(churn, { min_total_bytes: 10 ** 9 })).toEqual({ kind: "evaluated", findings: [] });
    });

    it("hotspot count and share minimums apply; invalid overrides fall back to defaults", () => {
      const strict = checkCodeC61(churn, { min_hotspot_count: 22000 });
      expect(strict).toEqual({ kind: "evaluated", findings: [] });
      const bad = checkCodeC61(churn, { churn_ratio_min: "x", min_hotspot_count: -5 });
      expect(bad.kind === "evaluated" && bad.findings.length).toBe(2);
      expect(C61_DEFAULTS.churn_ratio_min).toBe(20);
    });
  });

  describe("missing or malformed evidence is never 'clean'", () => {
    it.each([
      ["empty object", {}],
      ["no peak_memory", { ...churn, metadata: {} }],
      ["string total", { ...churn, total_bytes_allocated: "27423964" }],
      ["top list not an array", { ...churn, top_allocations_by_count: {} }],
      ["entry without count", { ...churn, top_allocations_by_count: [{ location: "f:a.py:1" }] }],
      ["negative total", { ...churn, total_bytes_allocated: -1 }],
    ])("%s -> unavailable", (_n, data) => {
      expect(checkCodeC61(data as Record<string, unknown>).kind).toBe("unavailable");
    });

    it("zero peak: evaluated with nothing to compare against, no divide-by-zero finding", () => {
      const data = structuredClone(churn) as any;
      data.metadata.peak_memory = 0;
      expect(checkCodeC61(data)).toEqual({ kind: "evaluated", findings: [] });
    });
  });

  describe("contract v1 through evaluate()", () => {
    it("completed with cited artifact fields that equal the supplied data", () => {
      const inp = input(churn);
      const r = evaluate(inp);
      expect(r.status).toBe("completed");
      expect(r.coverage.evaluated_scope).toEqual(["artifact:memray-stats.json"]);
      expect(r.coverage.limitations[0]).toMatch(/^Artifact analysis only/);
      expect(r.findings).toHaveLength(2);
      for (const f of r.findings) {
        expect(f.fingerprint).toMatch(/^[0-9a-f]{64}$/);
        expect(f.identity).not.toMatch(/:\d+$/);
        expect(f.references.every((u) => u.startsWith("http"))).toBe(true);
        for (const e of f.evidence) {
          expect(e.kind).toBe("artifact");
          expect(e.line_start).toBeUndefined();
          expect(e.value).toEqual((churn as any)[e.field!]);
        }
      }
      expect(r.measurements).toEqual([]);
    });

    it("steady run: completed with no findings; garbage: unavailable with a reason", () => {
      expect(evaluate(input(steady))).toMatchObject({ status: "completed", findings: [] });
      const bad = evaluate(input({ total_allocations: 3 }));
      expect(bad.status).toBe("unavailable");
      expect(bad.coverage.limitations.join(" ")).toMatch(/not a memray stats JSON export/);
    });

    it("no artifact source, or two of them, is unavailable (never completed)", () => {
      const none = { ...input(churn), sources: [] };
      expect(evaluate(none).status).toBe("unavailable");
      const two = input(churn);
      two.sources.push({ ...two.sources[0], source_id: "dup" });
      expect(evaluate(two).status).toBe("unavailable");
    });

    it("identities and fingerprints are stable across repeated evaluation", () => {
      const a = evaluate(input(churn)).findings.map((f) => f.fingerprint);
      const b = evaluate(input(structuredClone(churn))).findings.map((f) => f.fingerprint);
      expect(a).toEqual(b);
    });

    it("wrong detector_version is unavailable", () => {
      expect(evaluate({ ...input(churn), detector_version: "9.9.9" }).status).toBe("unavailable");
    });

    it.skipIf(!validatorAvailable)("pairs pass the shared validate_pair (churn, steady, unrecognised)", () => {
      for (const data of [churn, steady, { total_allocations: 3 }]) {
        const inp = input(data as Record<string, unknown>);
        const verdict = validatePair(inp as any, evaluate(inp));
        expect(verdict.ok, verdict.output).toBe(true);
      }
    });
  });
});
