import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { parsePythonSource } from "../src/core/parse.js";
import { checkCodeC12 } from "../src/checks/code-c1-2/index.js";
import { evaluate } from "../src/contract.js";
import { staticInput, validatePair, validatorAvailable } from "./helpers/contract.js";

const FIXTURES_DIR = join(__dirname, "fixtures", "code-c1-2");

function runCheckOnFixture(relPath: string) {
  const content = readFileSync(join(FIXTURES_DIR, relPath), "utf-8");
  const parsed = parsePythonSource(relPath, content);
  expect(parsed.hasSyntaxError).toBe(false);
  return checkCodeC12(parsed);
}

function run(content: string, path = "inline.py") {
  return checkCodeC12(parsePythonSource(path, content));
}

describe("CODE-C1.2 Redundant assignment detector", () => {
  describe("Positives fixture (C12-01 … C12-04)", () => {
    const findings = runCheckOnFixture("positives.py");
    const bySnippet = (s: string) => findings.find((f) => f.evidence.snippet === s);

    it("reports exactly one finding per positive statement", () => {
      expect(findings).toHaveLength(8);
      for (const f of findings) {
        expect(f.check).toBe("CODE-C1.2");
        expect(f.evidenceTier).toBe("static");
        expect(f.impact.quantified).toBe(false);
        expect(f.fingerprint).toMatch(/^[0-9a-f]{16}$/);
        expect(f.references.map((r) => r.id)).toEqual(["SRC-01", "taxonomy-c1.2"]);
        expect(f.limitations.some((l) => l.includes("hot loop"))).toBe(true);
      }
      expect(findings.filter((f) => f.kind === "self-assignment")).toHaveLength(4);
      expect(findings.filter((f) => f.kind === "dead-store")).toHaveLength(4);
    });

    it("S1 name and tuple self-assignment: Low severity, High confidence", () => {
      for (const snippet of ["x = x", "a, b = a, b"]) {
        const f = bySnippet(snippet);
        expect(f, snippet).toBeDefined();
        expect(f!.kind).toBe("self-assignment");
        expect(f!.severity).toBe("low");
        expect(f!.confidence).toBe("high");
        expect(f!.agentPrompt).toContain(`remove the self-assignment \`${snippet}\``);
      }
    });

    it("S1 attribute and subscript self-assignment: Medium confidence with the setter caveat", () => {
      for (const snippet of ["self.n = self.n", "row[i] = row[i]"]) {
        const f = bySnippet(snippet)!;
        expect(f.confidence).toBe("medium");
        expect(f.limitations.some((l) => l.includes("defaultdict"))).toBe(true);
        expect(f.agentPrompt).toContain("no property setter");
      }
    });

    it("S2 literal store overwritten: Low/High, location spans store to overwrite", () => {
      const f = bySnippet("total = 0")!;
      expect(f.kind).toBe("dead-store");
      expect(f.severity).toBe("low");
      expect(f.confidence).toBe("high");
      expect(f.evidence.symbol).toBe("total");
      expect(f.evidence.expr).toBe("total = sum(xs)");
      expect(f.location.endLine).toBe(f.location.startLine + 1);
      expect(f.agentPrompt).toContain("delete `total = 0`");
    });

    it("S2 call store overwritten in a loop: Medium/Medium, keep-the-call caveat, loop noted", () => {
      const f = bySnippet("data = load(p)")!;
      expect(f.severity).toBe("medium");
      expect(f.confidence).toBe("medium");
      expect(f.evidence.loopType).toBe("for");
      expect(f.why).toContain("computed by a call");
      expect(f.limitations.some((l) => l.includes("bare statement"))).toBe(true);
      expect(f.agentPrompt).toContain("keep `load(p)` as a bare statement");
    });

    it("S2 ignores attribute names and keyword names between the stores", () => {
      const f = bySnippet("mode = cfg.mode")!;
      expect(f.severity).toBe("low");
      expect(f.confidence).toBe("medium");
      expect(f.evidence.expr).toBe('mode = "safe"');
    });

    it("S2 annotated store: suggests moving the annotation", () => {
      const f = bySnippet("names: list[str] = []")!;
      expect(f.confidence).toBe("high");
      expect(f.agentPrompt).toContain("move the annotation");
    });
  });

  describe("Negatives fixture (C12-05 … C12-07)", () => {
    it("reports zero findings where every guard applies", () => {
      expect(runCheckOnFixture("negatives.py")).toEqual([]);
    });
  });

  describe("Boundaries (C12-08)", () => {
    it("an adjacent literal overwrite inside try cannot raise, so it is still flagged", () => {
      const f = run("def f():\n    try:\n        x = 1\n        x = 2\n    finally:\n        log(x)\n");
      expect(f).toHaveLength(1);
      expect(f[0].evidence.snippet).toBe("x = 1");
    });

    it("an f-string overwrite inside try can raise, so it is not flagged", () => {
      expect(run('def f(y):\n    try:\n        x = 1\n        x = f"{y}"\n    finally:\n        log(x)\n')).toEqual([]);
    });

    it("an empty built-in constructor is a literal-like store: Low/High", () => {
      const [f] = run("def f(a, b):\n    seen = set()\n    seen = a - b\n    return seen\n");
      expect(f.severity).toBe("low");
      expect(f.confidence).toBe("high");
      expect(f.agentPrompt).toContain("delete `seen = set()`");
    });

    it("chains report each overwritten store once", () => {
      const f = run("def f():\n    x = 1\n    x = 2\n    x = 3\n    return x\n");
      expect(f.map((x) => x.evidence.snippet)).toEqual(["x = 1", "x = 2"]);
    });

    it("a store never read and never overwritten is C1.5's, not C1.2's", () => {
      expect(run("def f():\n    rows = fetch_all()\n")).toEqual([]);
    });
  });

  describe("Robustness (C12-09)", () => {
    it("returns zero findings on invalid Python syntax", () => {
      const content = readFileSync(join(FIXTURES_DIR, "syntax_error.py"), "utf-8");
      const parsed = parsePythonSource("syntax_error.py", content);
      expect(parsed.hasSyntaxError).toBe(true);
      expect(checkCodeC12(parsed)).toEqual([]);
    });

    it("returns zero findings on an empty file", () => {
      expect(run("")).toEqual([]);
    });
  });

  describe("Fingerprints (C12-10)", () => {
    const store = "def f(xs):\n    total = 0\n    total = sum(xs)\n    return total\n";

    it("are stable across line shifts", () => {
      const before = run(store);
      const after = run("# header\n\n" + store);
      expect(before).toHaveLength(1);
      expect(after[0].fingerprint).toBe(before[0].fingerprint);
      expect(after[0].identity).toBe(before[0].identity);
    });

    it("differ for identical stores in different functions", () => {
      const twice = run(store + "\n\n" + store.replace("def f", "def g"));
      expect(twice).toHaveLength(2);
      expect(twice[0].fingerprint).not.toBe(twice[1].fingerprint);
    });

    it("`# noqa: CODE-C1.2` on the overwriting line suppresses", () => {
      expect(run(store.replace("total = sum(xs)", "total = sum(xs)  # noqa: CODE-C1.2"))).toEqual([]);
    });

    it("another check's noqa code does not suppress", () => {
      expect(run(store.replace("total = 0", "total = 0  # noqa: CODE-C3.2"))).toHaveLength(1);
    });
  });

  describe("Contract v1", () => {
    const read = (f: string) => readFileSync(join(FIXTURES_DIR, f), "utf-8");

    it("positives: completed with line-free identities; syntax error is never certified", () => {
      const result = evaluate(staticInput("CODE-C1.2", { "positives.py": read("positives.py") }));
      expect(result.status).toBe("completed");
      expect(result.findings).toHaveLength(runCheckOnFixture("positives.py").length);
      expect(result.findings.every((f) => /::(self-assignment|dead-store):/.test(f.identity))).toBe(true);

      const broken = evaluate(staticInput("CODE-C1.2", { "syntax_error.py": read("syntax_error.py") }));
      expect(broken.status).toBe("unavailable");
      expect(broken.findings).toEqual([]);
    });

    it.skipIf(!validatorAvailable)("positives, negatives and syntax-error pairs pass shared validate_pair", () => {
      for (const files of [
        { "positives.py": read("positives.py") },
        { "negatives.py": read("negatives.py") },
        { "positives.py": read("positives.py"), "syntax_error.py": read("syntax_error.py") },
      ]) {
        const input = staticInput("CODE-C1.2", files);
        const verdict = validatePair(input, evaluate(input));
        expect(verdict.ok, verdict.output).toBe(true);
      }
    });
  });
});
