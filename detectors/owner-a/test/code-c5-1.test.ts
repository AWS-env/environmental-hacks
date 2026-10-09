import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { parsePythonSource } from "../src/core/parse.js";
import { checkCodeC51 } from "../src/checks/code-c5-1/index.js";
import { evaluate } from "../src/contract.js";
import { staticInput, validatePair, validatorAvailable } from "./helpers/contract.js";

const FIXTURES_DIR = join(__dirname, "fixtures", "code-c5-1");

function runCheckOnFixture(relPath: string) {
  const content = readFileSync(join(FIXTURES_DIR, relPath), "utf-8");
  const parsed = parsePythonSource(relPath, content);
  expect(parsed.hasSyntaxError).toBe(false);
  return checkCodeC51(parsed);
}

function run(content: string, path = "inline.py") {
  return checkCodeC51(parsePythonSource(path, content));
}

const TEN = '["a", "b", "c", "d", "e", "f", "g", "h", "i", "j"]';

describe("CODE-C5.1 list membership in a loop detector", () => {
  describe("Positives fixture", () => {
    const findings = runCheckOnFixture("positives.py");
    const bySymbol = (s: string) => findings.filter((f) => f.evidence.symbol === s);

    it("reports exactly one finding per membership test", () => {
      expect(findings).toHaveLength(20);
      for (const f of findings) {
        expect(f.check).toBe("CODE-C5.1");
        expect(f.kind).toBe("list-membership-in-loop");
        expect(f.severity).toBe("medium");
        expect(f.evidenceTier).toBe("static");
        expect(f.impact.quantified).toBe(false);
        expect(f.fingerprint).toMatch(/^[0-9a-f]{16}$/);
        expect(f.detector).toEqual({ id: "owner-a-static-scan", version: "0.1.0" });
        expect(f.references.map((r) => r.id)).toEqual(["SRC-01", "taxonomy-c5.1"]);
        expect(f.identity).toMatch(/^list-membership-in-loop:[^:]+:[^:]+:\d+$/);
        expect(f.agentPrompt).toContain("set(");
        expect(f.limitations.join(" ")).toContain("not measured");
        expect(f.limitations.join(" ")).toContain("hashable");
      }
    });

    it("for loop with `not in` and a >8 literal: high confidence", () => {
      const f = bySymbol("allowed")[0];
      expect(f.evidence.loopType).toBe("for");
      expect(f.evidence.expr).toBe("row not in allowed");
      expect(f.confidence).toBe("high");
      expect(f.why).toContain("not in allowed");
    });

    it("while loop with list(...) binding: medium confidence", () => {
      const f = bySymbol("seen")[0];
      expect(f.evidence.loopType).toBe("while");
      expect(f.confidence).toBe("medium");
    });

    it("comprehension over a .split() result and a generator element", () => {
      expect(bySymbol("words")).toHaveLength(1);
      expect(bySymbol("words")[0].confidence).toBe("medium");
      const names = bySymbol("names").filter((f) => /:(generator_element|twice_same_scope):/.test(f.identity!));
      expect(names.length).toBeGreaterThanOrEqual(3);
      // list-comprehension binding is high confidence
      expect(names.every((f) => f.confidence === "high")).toBe(true);
    });

    it("annotated list/List/Sequence parameters are resolved", () => {
      expect(bySymbol("known")).toHaveLength(2);
      expect(bySymbol("known").every((f) => f.confidence === "medium")).toBe(true);
    });

    it("module-level list and sorted() bindings", () => {
      expect(bySymbol("BLOCKED")).toHaveLength(1);
      expect(bySymbol("BLOCKED")[0].confidence).toBe("high");
      expect(bySymbol("ordered")).toHaveLength(1);
    });

    it("self.attr chain bound in __init__", () => {
      const f = bySymbol("self.ids")[0];
      expect(f).toBeDefined();
      expect(f.identity).toBe("list-membership-in-loop:Registry.count:self.ids:0");
      expect(f.confidence).toBe("high");
    });

    it("repeated tests of one name in one scope get ordinals", () => {
      const twice = findings.filter((f) => f.identity!.includes(":twice_same_scope:names:"));
      expect(twice.map((f) => f.identity)).toEqual([
        "list-membership-in-loop:twice_same_scope:names:0",
        "list-membership-in-loop:twice_same_scope:names:1",
      ]);
      expect(twice[0].fingerprint).not.toBe(twice[1].fingerprint);
    });
  });

  describe("Negatives fixture (similar shapes, exceptions, unknown bindings)", () => {
    it("reports zero findings", () => {
      expect(runCheckOnFixture("negatives.py")).toEqual([]);
    });

    it("guards are what suppress (the unguarded twin fires)", () => {
      const base = `def f(rows):\n    names = ${TEN}\n    for r in rows:\n        if r in names:\n            MUT\n`;
      expect(run(base.replace("MUT", "pass"))).toHaveLength(1);
      expect(run(base.replace("MUT", "names.append(r)"))).toEqual([]);
      expect(run(base.replace("MUT", "names += [r]"))).toEqual([]);
      expect(run(base.replace("MUT", "names[0] = r"))).toEqual([]);
      expect(run(base.replace("MUT", "del names[0]"))).toEqual([]);
      expect(run(base.replace("names = " + TEN, "names = {" + TEN.slice(1, -1) + "}").replace("MUT", "pass"))).toEqual([]);
    });

    it("mutation after an inner loop of an outer loop still blocks the finding", () => {
      const src = `def f(rows, cols):\n    names = list(rows)\n    for r in rows:\n        for c in cols:\n            if c in names:\n                pass\n        names.append(r)\n`;
      expect(run(src)).toEqual([]);
    });

    it("mutation outside the loop does not block", () => {
      const src = `def f(rows):\n    names = list(rows)\n    names.append(1)\n    for r in rows:\n        if r in names:\n            pass\n`;
      expect(run(src)).toHaveLength(1);
    });
  });

  describe("Small static trip count is not flagged", () => {
    const body = (iter: string, pre = "") =>
      `def f(src):\n    names = list(src)\n${pre}    for p in ${iter}:\n        if p in names:\n            print(p)\n`;
    const nine = '["a", "b", "c", "d", "e", "f", "g", "h", "i"]';

    it("(1) literal list/tuple/set/dict with <= 8 elements; boundary 8 vs 9", () => {
      expect(run(body('["/a", "/b", "/c"]'))).toEqual([]);
      expect(run(body("(1, 2)"))).toEqual([]);
      expect(run(body("{1, 2, 3}"))).toEqual([]);
      expect(run(body('{"a": 1, "b": 2}'))).toEqual([]);
      expect(run(body("[1, 2, 3, 4, 5, 6, 7, 8]"))).toEqual([]);
      expect(run(body(nine))).toHaveLength(1);
      expect(run(body("(1, 2, 3, 4, 5, 6, 7, 8, 9)"))).toHaveLength(1);
    });

    it("splat or comprehension elements make the size unknown", () => {
      expect(run(body("[*src, 1]"))).toHaveLength(1);
      expect(run(body("[x for x in src]"))).toHaveLength(1);
    });

    it("(2) name bound only to a small literal; mutated or non-literal bindings stay flagged", () => {
      expect(run(body("kinds", "    kinds = [1, 2, 3]\n"))).toEqual([]);
      expect(run(body("kinds", "    kinds = [1, 2, 3]\n    kinds.append(4)\n"))).toHaveLength(1);
      expect(run(body("kinds", "    kinds = [1, 2, 3]\n    kinds = list(src)\n"))).toHaveLength(1);
      expect(run(body("kinds", "    kinds = " + nine + "\n"))).toHaveLength(1);
    });

    it("(3) dict literal .items()/.keys()/.values() with <= 8 entries", () => {
      for (const m of ["items", "keys", "values"]) {
        expect(run(body(`d.${m}()`, '    d = {"a": 1, "b": 2}\n'))).toEqual([]);
      }
      const big = "{" + Array.from({ length: 9 }, (_, i) => `${i}: ${i}`).join(", ") + "}";
      expect(run(body("d.items()", `    d = ${big}\n`))).toHaveLength(1);
      expect(run(body("d.items()", '    d = {"a": 1}\n    d["b"] = 2\n'))).toHaveLength(1);
      expect(run(body("d.items()", '    d = {"a": 1}\n    d.update(src)\n'))).toHaveLength(1);
      expect(run(body("d.items()", '    d = {**src}\n'))).toHaveLength(1);
    });

    it("(4) range of integer literals with <= 8 iterations; boundary 8 vs 9", () => {
      expect(run(body("range(5)"))).toEqual([]);
      expect(run(body("range(8)"))).toEqual([]);
      expect(run(body("range(2, 6)"))).toEqual([]);
      expect(run(body("range(0, 16, 2)"))).toEqual([]);
      expect(run(body("range(9)"))).toHaveLength(1);
      expect(run(body("range(len(src))"))).toHaveLength(1);
    });

    it("self-derived rebinding (jupyter shape) stays small; other sources or two clauses do not", () => {
      const f = (rebind: string) =>
        `def f(src, big):\n    inc = list(src)\n    d = {"a": 1, "b": 2}\n    d = ${rebind}\n    return {k: v for k, v in d.items() if k in inc}\n`;
      expect(run(f("{k: v for k, v in d.items() if k in inc}"))).toEqual([]);
      expect(run(f("{k: v for k, v in d.items() if k in inc}")).length).toBe(0);
      expect(run(f("{k: v for k, v in big.items() if k}"))).toHaveLength(1);
      expect(run(f("{k: v for k in big for v in d.values()}"))).toHaveLength(1);
    });

    it("comprehensions follow the same rule", () => {
      const c = (iter: string) => `def f(src):\n    names = list(src)\n    return [p for p in ${iter} if p in names]\n`;
      expect(run(c("(1, 2, 3)"))).toEqual([]);
      expect(run(c("range(3)"))).toEqual([]);
      expect(run(c("src"))).toHaveLength(1);
    });

    it("while loops and a large outer loop are unaffected", () => {
      expect(run("def f(src):\n    names = list(src)\n    while src:\n        if src.pop() in names:\n            pass\n")).toHaveLength(1);
      const outer = "def f(src, rows):\n    names = list(src)\n    for r in rows:\n        for c in (1, 2, 3):\n            if c in names:\n                pass\n";
      expect(run(outer)).toHaveLength(1);
    });
  });
  describe("Missing / unknown binding", () => {
    it("unannotated parameter, import and undefined names report nothing", () => {
      expect(run("def f(rows, known):\n    return [r for r in rows if r in known]\n")).toEqual([]);
      expect(run("from m import xs\ndef f(rows):\n    return [r for r in rows if r in xs]\n")).toEqual([]);
      expect(run("def f(rows):\n    return [r for r in rows if r in nothing]\n")).toEqual([]);
      expect(run("def f(self, rows):\n    return [r for r in rows if r in self.unknown]\n")).toEqual([]);
    });

    it("a local rebinding shadows the module-level list", () => {
      const src = `XS = ${TEN}\ndef f(rows, XS):\n    return [r for r in rows if r in XS]\n`;
      expect(run(src)).toEqual([]);
    });

    it("a loop-variable or unpacked binding of the name is unknown", () => {
      const src = `def f(rows, groups):\n    for names in groups:\n        pass\n    return [r for r in rows if r in names]\n`;
      expect(run(src)).toEqual([]);
    });
  });

  describe("Boundary: literal size gate", () => {
    const src = (n: number) =>
      `def f(rows):\n    names = [${Array.from({ length: n }, (_, i) => i).join(", ")}]\n    return [r for r in rows if r in names]\n`;

    it("exactly 8 elements is not flagged", () => {
      expect(run(src(8))).toEqual([]);
    });

    it("9 elements is flagged with high confidence", () => {
      const out = run(src(9));
      expect(out).toHaveLength(1);
      expect(out[0].confidence).toBe("high");
    });

    it("tuple literal follows the same gate", () => {
      expect(run("def f(rows):\n    t = (1, 2, 3)\n    return [r for r in rows if r in t]\n")).toEqual([]);
      expect(run("def f(rows):\n    t = (1, 2, 3, 4, 5, 6, 7, 8, 9)\n    return [r for r in rows if r in t]\n")).toHaveLength(1);
    });
  });

  describe("Robustness", () => {
    it("returns zero findings on invalid Python syntax", () => {
      const content = readFileSync(join(FIXTURES_DIR, "syntax_error.py"), "utf-8");
      const parsed = parsePythonSource("syntax_error.py", content);
      expect(parsed.hasSyntaxError).toBe(true);
      expect(checkCodeC51(parsed)).toEqual([]);
    });

    it("returns zero findings on an empty file", () => {
      expect(run("")).toEqual([]);
    });
  });

  describe("Fingerprints and suppression", () => {
    const src = "def f(rows):\n    names = list(rows)\n    for r in rows:\n        if r in names:\n            pass\n";

    it("are stable across line shifts", () => {
      const before = run(src);
      const after = run("# header\n\nimport os\n\n" + src);
      expect(before).toHaveLength(1);
      expect(after[0].fingerprint).toBe(before[0].fingerprint);
      expect(after[0].identity).toBe(before[0].identity);
      expect(after[0].identity).toBe("list-membership-in-loop:f:names:0");
      expect(after[0].location.startLine).not.toBe(before[0].location.startLine);
    });

    it("differ by path", () => {
      expect(run(src, "a.py")[0].fingerprint).not.toBe(run(src, "b.py")[0].fingerprint);
    });

    it("`# noqa: CODE-C5.1` on the loop header or the test line suppresses; another code does not", () => {
      expect(run(src.replace("in rows:", "in rows:  # noqa: CODE-C5.1"))).toEqual([]);
      expect(run(src.replace("in names:", "in names:  # noqa: CODE-C5.1"))).toEqual([]);
      expect(run(src.replace("in names:", "in names:  # noqa"))).toEqual([]);
      expect(run(src.replace("in names:", "in names:  # noqa: F401"))).toHaveLength(1);
    });
  });

  describe("Contract v1", () => {
    const read = (f: string) => readFileSync(join(FIXTURES_DIR, f), "utf-8");

    it("positives: completed with line-free identities; syntax error is never certified", () => {
      const result = evaluate(staticInput("CODE-C5.1", { "positives.py": read("positives.py") }));
      expect(result.status).toBe("completed");
      expect(result.findings).toHaveLength(runCheckOnFixture("positives.py").length);
      expect(result.findings.every((f) => /list-membership-in-loop:[^:]+:[^:]+:\d+$/.test(f.identity))).toBe(true);
      expect(result.findings.every((f) => !/\bline\b|:\d{2,}:\d+$/.test(f.identity))).toBe(true);

      const clean = evaluate(staticInput("CODE-C5.1", { "negatives.py": read("negatives.py") }));
      expect(clean.status).toBe("completed");
      expect(clean.findings).toEqual([]);

      const broken = evaluate(staticInput("CODE-C5.1", { "syntax_error.py": read("syntax_error.py") }));
      expect(broken.status).toBe("unavailable");
      expect(broken.findings).toEqual([]);

      const partial = evaluate(
        staticInput("CODE-C5.1", { "positives.py": read("positives.py"), "syntax_error.py": read("syntax_error.py") })
      );
      expect(partial.status).toBe("partial");
      expect(partial.status).not.toBe("completed");
    });

    it.skipIf(!validatorAvailable)("positives, negatives and syntax-error pairs pass shared validate_pair", () => {
      for (const files of [
        { "positives.py": read("positives.py") },
        { "negatives.py": read("negatives.py") },
        { "syntax_error.py": read("syntax_error.py") },
        { "positives.py": read("positives.py"), "syntax_error.py": read("syntax_error.py") },
      ]) {
        const input = staticInput("CODE-C5.1", files);
        const verdict = validatePair(input, evaluate(input));
        expect(verdict.ok, verdict.output).toBe(true);
      }
    });
  });
});



