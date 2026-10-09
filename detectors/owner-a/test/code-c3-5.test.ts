import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { parsePythonSource } from "../src/core/parse.js";
import { checkCodeC35 } from "../src/checks/code-c3-5/index.js";

const FIXTURES_DIR = join(__dirname, "fixtures", "code-c3-5");

function runCheckOnFixture(relPath: string) {
  const content = readFileSync(join(FIXTURES_DIR, relPath), "utf-8");
  const parsed = parsePythonSource(relPath, content);
  expect(parsed.hasSyntaxError).toBe(false);
  return checkCodeC35(parsed);
}

function run(content: string, path = "inline.py") {
  return checkCodeC35(parsePythonSource(path, content));
}

describe("CODE-C3.5 Missing loop early exit detector", () => {
  describe("Positives fixture (C35-01 … C35-04)", () => {
    const findings = runCheckOnFixture("positives.py");
    const bySymbol = (s: string) => findings.find((f) => f.evidence.symbol === s);

    it("reports exactly one finding per positive loop", () => {
      expect(findings).toHaveLength(5);
      for (const f of findings) {
        expect(f.check).toBe("CODE-C3.5");
        expect(f.kind).toBe("missing-early-exit");
        expect(f.severity).toBe("medium");
        expect(f.evidenceTier).toBe("static");
        expect(f.impact.quantified).toBe(false);
        expect(f.fingerprint).toMatch(/^[0-9a-f]{16}$/);
        expect(f.references.map((r) => r.id)).toEqual(["SRC-01", "taxonomy-c3.5"]);
        expect(f.limitations.some((l) => l.includes("match lands early"))).toBe(true);
        expect(f.evidence.snippet.startsWith("for ")).toBe(true);
      }
    });

    it("S1 sticky flag / constant: High confidence, suggests break or any()", () => {
      for (const symbol of ["found", "status", "seen"]) {
        const f = bySymbol(symbol);
        expect(f, symbol).toBeDefined();
        expect(f!.confidence).toBe("high");
        expect(f!.agentPrompt).toContain("break");
        expect(f!.limitations.some((l) => l.includes("first match"))).toBe(false);
      }
      expect(bySymbol("found")!.agentPrompt).toContain("any(<condition> for x in items)");
    });

    it("S2 match store: Medium confidence with the first-vs-last caveat", () => {
      const f = bySymbol("result")!;
      expect(f.confidence).toBe("medium");
      expect(f.limitations.some((l) => l.includes("keeps the last"))).toBe(true);
      expect(f.agentPrompt).toContain("next((<value> for x in items if <condition>), result)");
    });

    it("S3 store then return: suggests returning from inside the loop", () => {
      const f = bySymbol("match")!;
      expect(f.confidence).toBe("medium");
      expect(f.agentPrompt).toContain("return the match from inside the loop");
    });

    it("attributes a nested flag loop to the inner loop only", () => {
      const f = bySymbol("seen")!;
      expect(f.evidence.snippet).toBe("for x in g:");
    });
  });

  describe("Negatives fixture (C35-05 … C35-07)", () => {
    it("reports zero findings where every guard applies", () => {
      expect(runCheckOnFixture("negatives.py")).toEqual([]);
    });
  });

  describe("Boundary with C3.6", () => {
    it("any([...]) over a list comprehension is not a C3.5 finding", () => {
      expect(run("def f(xs):\n    return any([p(x) for x in xs])\n")).toEqual([]);
    });
  });

  describe("Robustness (C35-08)", () => {
    it("returns zero findings on invalid Python syntax", () => {
      const content = readFileSync(join(FIXTURES_DIR, "syntax_error.py"), "utf-8");
      const parsed = parsePythonSource("syntax_error.py", content);
      expect(parsed.hasSyntaxError).toBe(true);
      expect(checkCodeC35(parsed)).toEqual([]);
    });

    it("returns zero findings on an empty file", () => {
      expect(run("")).toEqual([]);
    });
  });

  describe("Fingerprints (C35-09)", () => {
    const loop = "def f(xs):\n    found = False\n    for x in xs:\n        if p(x):\n            found = True\n    return found\n";

    it("are stable across line shifts", () => {
      const before = run(loop);
      const after = run("# header\n\n" + loop);
      expect(before).toHaveLength(1);
      expect(after[0].fingerprint).toBe(before[0].fingerprint);
    });

    it("`# noqa: CODE-C3.5` on the flag assignment suppresses", () => {
      expect(run(loop.replace("found = True", "found = True  # noqa: CODE-C3.5"))).toEqual([]);
    });
  });
});
