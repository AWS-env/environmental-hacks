import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { parsePythonSource } from "../src/core/parse.js";
import { checkCodeC32 } from "../src/checks/code-c3-2/index.js";

const FIXTURES_DIR = join(__dirname, "fixtures", "code-c3-2");

function runCheckOnFixture(relPath: string) {
  const fullPath = join(FIXTURES_DIR, relPath);
  const content = readFileSync(fullPath, "utf-8");
  const parsed = parsePythonSource(relPath, content);
  return checkCodeC32(parsed);
}

describe("CODE-C3.2 Recomputing loop-invariant detector", () => {
  describe("Positives fixture", () => {
    it("detects invariant calls, chains, and arithmetic in positives.py", () => {
      const findings = runCheckOnFixture("positives.py");

      expect(findings.length).toBeGreaterThanOrEqual(6);

      // 1. S1 Invariant call in for loop
      const s1ForFinding = findings.find(
        (f) =>
          f.evidence.expr === "compute_rate(cfg)" &&
          f.evidence.loopType === "for" &&
          f.location.startLine < 12
      );
      expect(s1ForFinding).toBeDefined();
      expect(s1ForFinding?.check).toBe("CODE-C3.2");
      expect(s1ForFinding?.kind).toBe("loop-invariant-recomputation");
      expect(s1ForFinding?.severity).toBe("medium");
      expect(s1ForFinding?.confidence).toBe("medium");
      expect(s1ForFinding?.evidence.symbol).toBe("cfg");
      expect(s1ForFinding?.limitations).toContain(
        "hoist only if side-effect free — verify callee purity"
      );
      expect(s1ForFinding?.references.map((r) => r.id)).toEqual([
        "SRC-01",
        "taxonomy-c3.2",
      ]);
      expect(s1ForFinding?.agentPrompt).toContain("compute_rate(cfg)");

      // 2. S1 Invariant call in while loop
      const s1WhileFinding = findings.find(
        (f) =>
          f.evidence.expr === "fetch_constant_lookup(cfg)" &&
          f.evidence.loopType === "while"
      );
      expect(s1WhileFinding).toBeDefined();
      expect(s1WhileFinding?.severity).toBe("medium");
      expect(s1WhileFinding?.confidence).toBe("medium");
      expect(s1WhileFinding?.evidence.symbol).toBe("cfg");

      // 3. S2 Invariant attribute / subscript chain
      const s2ChainFinding = findings.find(
        (f) => f.evidence.expr === 'settings.limits["max"]'
      );
      expect(s2ChainFinding).toBeDefined();
      expect(s2ChainFinding?.severity).toBe("low");
      expect(s2ChainFinding?.confidence).toBe("medium");
      expect(s2ChainFinding?.evidence.symbol).toBe("settings");
      expect(
        s2ChainFinding?.limitations.some((l) => l.includes("__getitem__"))
      ).toBe(true);

      // 4. S3 Invariant arithmetic
      const s3ArithmeticFinding = findings.find(
        (f) => f.evidence.expr === "base + margin"
      );
      expect(s3ArithmeticFinding).toBeDefined();
      expect(s3ArithmeticFinding?.severity).toBe("low");
      expect(s3ArithmeticFinding?.confidence).toBe("high");
      expect(s3ArithmeticFinding?.evidence.symbol).toBe("base");

      // 5. Nested loop: invariant in both loops -> attributed to outer loop
      const outerNested = findings.find(
        (f) =>
          f.evidence.expr === "compute_rate(cfg)" &&
          f.location.startLine >= 37 &&
          f.location.startLine <= 45
      );
      expect(outerNested).toBeDefined();
      expect(outerNested?.fingerprint).toBeDefined();

      // 6. Nested loop: invariant only in inner loop -> attributed to inner loop
      const innerNested = findings.find(
        (f) =>
          f.evidence.expr === "compute_rate(outer)" &&
          f.location.startLine >= 46
      );
      expect(innerNested).toBeDefined();
      expect(innerNested?.evidence.symbol).toBe("outer");
    });
  });

  describe("Negatives fixture", () => {
    it("reports zero findings on negatives.py across all guards", () => {
      const findings = runCheckOnFixture("negatives.py");
      expect(findings).toEqual([]);
    });
  });

  describe("Audit regressions (2026-10-09)", () => {
    const run = (src: string) => checkCodeC32(parsePythonSource("a.py", src));

    it("does not flag statement-level calls run for their side effects", () => {
      expect(run("for r in rows:\n    print(header)\n    notify(cfg)\n    use(r)\n")).toEqual([]);
    });

    it("does not flag awaited calls", () => {
      expect(
        run("async def f(rows, cfg):\n    for r in rows:\n        x = (await load(cfg)) + r\n")
      ).toEqual([]);
    });

    it("leaves single-hop lookups to C10.5 but flags two-hop chains", () => {
      expect(run("for item in items:\n    total += item.price * rate.value\n")).toEqual([]);
      expect(run("for item in items:\n    total += item.price * cfg['rate']\n")).toEqual([]);
      const twoHop = run("for item in items:\n    total += item.price * cfg.rates['eu']\n");
      expect(twoHop).toHaveLength(1);
      expect(twoHop[0].evidence.expr).toBe("cfg.rates['eu']");
    });

    it("reports a repeated expression once per loop", () => {
      const findings = run("for r in rows:\n    a = f(cfg) + r\n    b = f(cfg) - r\n");
      expect(findings).toHaveLength(1);
    });

    it("gives identical-header loops in one scope distinct fingerprints", () => {
      const findings = run(
        "def g(rows, cfg):\n    for r in rows:\n        a = f(cfg) + r\n    for r in rows:\n        b = f(cfg) - r\n"
      );
      expect(findings).toHaveLength(2);
      expect(findings[0].fingerprint).not.toBe(findings[1].fingerprint);
    });

    it("does not widen the default noqa codes (a C3.2 noqa must not hide C1.1)", async () => {
      const { isLineSuppressed } = await import("../src/core/suppressions.js");
      expect(isLineSuppressed("import os  # noqa: CODE-C3.2").isSuppressed).toBe(false);
      expect(isLineSuppressed("x = f(cfg)  # noqa: CODE-C3.2", ["CODE-C3.2"]).isSuppressed).toBe(true);
    });
  });

  describe("Robustness against syntax errors and empty files", () => {
    it("gracefully returns zero findings on invalid Python syntax without throwing", () => {
      const findings = runCheckOnFixture("syntax_error.py");
      expect(findings).toEqual([]);
    });

    it("gracefully handles empty source files", () => {
      const parsed = parsePythonSource("empty.py", "");
      const findings = checkCodeC32(parsed);
      expect(findings).toEqual([]);
    });
  });

  describe("Deterministic and line-independent fingerprints", () => {
    it("generates stable 16-character hex fingerprints that remain stable across line shifts", () => {
      const code1 = `
def calc(items, cfg):
    for x in items:
        val = compute_rate(cfg) * x
`;
      const code2 = `
# Added header comments


def calc(items, cfg):
    # Added extra spacing

    for x in items:
        val = compute_rate(cfg) * x
`;
      const parsed1 = parsePythonSource("calc.py", code1);
      const parsed2 = parsePythonSource("calc.py", code2);

      const findings1 = checkCodeC32(parsed1);
      const findings2 = checkCodeC32(parsed2);

      expect(findings1).toHaveLength(1);
      expect(findings2).toHaveLength(1);
      expect(findings1[0].fingerprint).toHaveLength(16);
      expect(findings1[0].fingerprint).toBe(findings2[0].fingerprint);
    });
  });
});
