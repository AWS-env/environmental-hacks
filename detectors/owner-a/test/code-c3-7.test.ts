import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { parsePythonSource } from "../src/core/parse.js";
import { checkCodeC37 } from "../src/checks/code-c3-7/index.js";
import { evaluate } from "../src/contract.js";
import { staticInput, validatePair, validatorAvailable } from "./helpers/contract.js";

const FIXTURES_DIR = join(__dirname, "fixtures", "code-c3-7");

function runCheckOnFixture(relPath: string) {
  const content = readFileSync(join(FIXTURES_DIR, relPath), "utf-8");
  const parsed = parsePythonSource(relPath, content);
  expect(parsed.hasSyntaxError).toBe(false);
  return checkCodeC37(parsed);
}

function run(content: string, path = "inline.py") {
  return checkCodeC37(parsePythonSource(path, content));
}

describe("CODE-C3.7 Inefficient array mutation detector", () => {
  describe("Positives fixture (C37-01 … C37-04)", () => {
    const findings = runCheckOnFixture("positives.py");
    const bySymbol = (s: string) => findings.find((f) => f.evidence.symbol === s);

    it("reports exactly one finding per positive mutation", () => {
      expect(findings).toHaveLength(9);
      for (const f of findings) {
        expect(f.check).toBe("CODE-C3.7");
        expect(f.evidenceTier).toBe("static");
        expect(f.impact.quantified).toBe(false);
        expect(f.fingerprint).toMatch(/^[0-9a-f]{16}$/);
        expect(f.references.map((r) => r.id)).toEqual(["SRC-01", "taxonomy-c3.7"]);
        expect(f.evidence.mutator).toBeTruthy();
        expect(f.evidence.loopType).toMatch(/^(for|while)$/);
      }
    });

    it("S1 remove/delete from the iterated collection: High / High, names the correctness risk", () => {
      for (const symbol of ["items", "d", "cache", "self.handlers"]) {
        const f = bySymbol(symbol);
        expect(f, symbol).toBeDefined();
        expect(f!.kind).toBe("mutate-during-iteration");
        expect(f!.severity).toBe("high");
        expect(f!.confidence).toBe("high");
        expect(f!.why).toContain("RuntimeError");
        expect(f!.why).toContain("skips");
      }
      expect(bySymbol("d")!.evidence.mutator).toBe("del");
      expect(bySymbol("cache")!.evidence.mutator).toBe("pop");
      expect(bySymbol("items")!.agentPrompt).toContain("for x in list(items):");
    });

    it("S2 front re-indexing: High; confidence follows the visible binding", () => {
      const queue = bySymbol("queue")!;
      expect(queue.kind).toBe("front-reindex-in-loop");
      expect(queue.evidence).toMatchObject({ mutator: "pop(0)", loopType: "while" });
      expect(queue.confidence).toBe("high");
      expect(queue.agentPrompt).toContain("popleft()");

      const out = bySymbol("out")!;
      expect(out.evidence.mutator).toBe("insert(0)");
      expect(out.confidence).toBe("high");
      expect(out.agentPrompt).toContain("appendleft");

      const buf = bySymbol("buf")!;
      expect(buf.evidence.mutator).toBe("del [0]");
      expect(buf.confidence).toBe("medium");
      expect(buf.limitations.some((l) => l.includes("collections.deque"))).toBe(true);
    });

    it("S3 append into the iterated list is Low (worklist caveat)", () => {
      const f = bySymbol("todo")!;
      expect(f.severity).toBe("low");
      expect(f.confidence).toBe("medium");
      expect(f.limitations.some((l) => l.includes("worklist"))).toBe(true);
    });

    it("S4 slice store on the iterated list is Medium", () => {
      const f = bySymbol("a")!;
      expect(f.evidence.mutator).toBe("slice-store");
      expect(f.severity).toBe("medium");
    });
  });

  describe("Negatives fixture (C37-05 … C37-07)", () => {
    it("reports zero findings where every guard applies", () => {
      expect(runCheckOnFixture("negatives.py")).toEqual([]);
    });

    it("guards are what suppress (the unguarded twin fires)", () => {
      expect(run("def f(q):\n    while q:\n        q.pop(0)\n")).toHaveLength(1);
      expect(run("def f():\n    q = deque()\n    while q:\n        q.pop(0)\n")).toEqual([]);
      const once = "def f(items):\n    for x in items:\n        if x.bad:\n            items.remove(x)\n            EXIT\n";
      expect(run(once.replace("EXIT", "log(x)"))).toHaveLength(1);
      expect(run(once.replace("EXIT", "break"))).toEqual([]);
    });

    it("a break that belongs to a nested loop does not guard the outer mutation", () => {
      const src = "def f(items):\n    for x in items:\n        items.remove(x)\n        for y in x:\n            break\n";
      expect(run(src)).toHaveLength(1);
    });
  });

  describe("Robustness (C37-08)", () => {
    it("returns zero findings on invalid Python syntax", () => {
      const content = readFileSync(join(FIXTURES_DIR, "syntax_error.py"), "utf-8");
      const parsed = parsePythonSource("syntax_error.py", content);
      expect(parsed.hasSyntaxError).toBe(true);
      expect(checkCodeC37(parsed)).toEqual([]);
    });

    it("returns zero findings on an empty file", () => {
      expect(run("")).toEqual([]);
    });
  });

  describe("Fingerprints (C37-09)", () => {
    const src = "def f(items):\n    for x in items:\n        items.remove(x)\n";

    it("are stable across line shifts", () => {
      const before = run(src);
      const after = run("# header\n\n" + src);
      expect(before).toHaveLength(1);
      expect(after[0].fingerprint).toBe(before[0].fingerprint);
    });

    it("identical mutations in one scope get distinct fingerprints", () => {
      const [a, b] = run(src + "    for x in items:\n        items.remove(x)\n");
      expect(a.fingerprint).not.toBe(b.fingerprint);
    });

    it("`# noqa: CODE-C3.7` on the loop header or the mutation line suppresses", () => {
      expect(run(src.replace("in items:", "in items:  # noqa: CODE-C3.7"))).toEqual([]);
      expect(run(src.replace("remove(x)", "remove(x)  # noqa: CODE-C3.7"))).toEqual([]);
    });
  });

  describe("Contract v1", () => {
    const read = (f: string) => readFileSync(join(FIXTURES_DIR, f), "utf-8");

    it("positives: completed with line-free identities; syntax error is never certified", () => {
      const result = evaluate(staticInput("CODE-C3.7", { "positives.py": read("positives.py") }));
      expect(result.status).toBe("completed");
      expect(result.findings).toHaveLength(runCheckOnFixture("positives.py").length);
      expect(result.findings.every((f) => /(mutate-during-iteration|front-reindex-in-loop):/.test(f.identity))).toBe(true);

      const broken = evaluate(staticInput("CODE-C3.7", { "syntax_error.py": read("syntax_error.py") }));
      expect(broken.status).toBe("unavailable");
      expect(broken.findings).toEqual([]);
    });

    it.skipIf(!validatorAvailable)("positives, negatives and syntax-error pairs pass shared validate_pair", () => {
      for (const files of [
        { "positives.py": read("positives.py") },
        { "negatives.py": read("negatives.py") },
        { "positives.py": read("positives.py"), "syntax_error.py": read("syntax_error.py") },
      ]) {
        const input = staticInput("CODE-C3.7", files);
        const verdict = validatePair(input, evaluate(input));
        expect(verdict.ok, verdict.output).toBe(true);
      }
    });
  });
});
