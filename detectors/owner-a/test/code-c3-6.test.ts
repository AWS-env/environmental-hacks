import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { parsePythonSource } from "../src/core/parse.js";
import { checkCodeC36 } from "../src/checks/code-c3-6/index.js";
import { evaluate } from "../src/contract.js";
import { staticInput, validatePair, validatorAvailable } from "./helpers/contract.js";

const FIXTURES_DIR = join(__dirname, "fixtures", "code-c3-6");

function runCheckOnFixture(relPath: string) {
  const content = readFileSync(join(FIXTURES_DIR, relPath), "utf-8");
  const parsed = parsePythonSource(relPath, content);
  expect(parsed.hasSyntaxError).toBe(false);
  return checkCodeC36(parsed);
}

function run(content: string, path = "inline.py") {
  return checkCodeC36(parsePythonSource(path, content));
}

describe("CODE-C3.6 Unfiltered bulk iteration detector", () => {
  describe("Positives fixture (C36-01 … C36-04)", () => {
    const findings = runCheckOnFixture("positives.py");
    const atLine = (line: number) => findings.find((f) => f.location.startLine === line);

    it("reports exactly one finding per positive producer", () => {
      expect(findings).toHaveLength(11);
      for (const f of findings) {
        expect(f.check).toBe("CODE-C3.6");
        expect(f.evidenceTier).toBe("static");
        expect(f.impact.quantified).toBe(false);
        expect(f.severity).not.toBe("high");
        expect(f.fingerprint).toMatch(/^[0-9a-f]{16}$/);
        expect(f.references.map((r) => r.id)).toEqual(["SRC-01", "taxonomy-c3.6"]);
        expect(f.limitations.some((l) => l.includes("not visible statically"))).toBe(true);
      }
    });

    it("S1 take a prefix: [0], [:k], list(map)[:k], next(iter(...))", () => {
      for (const line of [6, 10, 15, 19, 23]) {
        const f = atLine(line);
        expect(f, `line ${line}`).toBeDefined();
        expect(f!.kind).toBe("eager-then-prefix");
        expect(f!.confidence).toBe("high");
      }
      expect(atLine(6)!.agentPrompt).toContain("`next(f(x) for x in xs if p(x))`");
      expect(atLine(10)!.agentPrompt).toContain("`list(itertools.islice((score(x) for x in xs), 10))`");
      expect(atLine(15)!.agentPrompt).toContain("itertools.islice(map(score, xs), k)");
    });

    it("trivial projection is Low; call-bearing producers are Medium", () => {
      expect(atLine(23)!.severity).toBe("low");
      expect(atLine(23)!.limitations.some((l) => l.includes("Trivial projection"))).toBe(true);
      expect(atLine(6)!.severity).toBe("medium");
    });

    it("S2 stop the loop early: readlines() + break, one-hop name + return", () => {
      const file = atLine(27)!;
      expect(file.kind).toBe("eager-then-early-exit");
      expect(file.confidence).toBe("medium");
      expect(file.agentPrompt).toContain("`for line in f:`");

      const hop = atLine(34)!;
      expect(hop.kind).toBe("eager-then-early-exit");
      expect(hop.evidence.symbol).toBe("rows");
      expect(hop.evidence.snippet).toBe("rows = [parse(l) for l in lines]\nfor r in rows:");
      expect(hop.agentPrompt).toContain("consumed at line 35");
      expect(hop.limitations.some((l) => l.includes("iterated only once"))).toBe(true);
    });

    it("S3 short-circuit test: any / all / in / not in", () => {
      for (const line of [42, 46, 50, 54]) {
        const f = atLine(line);
        expect(f, `line ${line}`).toBeDefined();
        expect(f!.kind).toBe("eager-then-short-circuit");
        expect(f!.confidence).toBe("high");
      }
      expect(atLine(42)!.agentPrompt).toContain("`(is_bad(x) for x in xs)`");
    });
  });

  describe("Negatives fixture (C36-05 … C36-07)", () => {
    it("reports zero findings where every guard applies", () => {
      expect(runCheckOnFixture("negatives.py")).toEqual([]);
    });

    it("guards are what suppress (the unguarded twin fires)", () => {
      expect(run("def f(xs):\n    return [check(v) for v in xs][0]\n")).toHaveLength(1);
      expect(run("def f(xs):\n    return [check(v) for v in (a, b, c)][0]\n")).toEqual([]);
      expect(run("def f(xs):\n    return any([check(x) for x in xs])\n")).toHaveLength(1);
      expect(run("def f(xs, out):\n    return any([out.append(x) for x in xs])\n")).toEqual([]);
      const loop = "def f(items):\n    for x in [i for i in items if i.stale]:\n        MUTATE\n        break\n";
      expect(run(loop.replace("MUTATE", "seen(x)"))).toHaveLength(1);
      expect(run(loop.replace("MUTATE", "items.remove(x)"))).toEqual([]);
    });

    it("return inside a nested loop still exits the outer loop (finding)", () => {
      const src = "def f(groups):\n    for g in [load(x) for x in groups]:\n        for i in g:\n            if i.bad:\n                return i\n";
      expect(run(src)).toHaveLength(1);
    });
  });

  describe("Boundaries with sibling checks", () => {
    it("emits nothing for shapes owned by C6.3 / C7.4 / C3.5 / C3.1 / C10.2 / C5.1 / C3.7 / C9.2", () => {
      expect(runCheckOnFixture("boundaries.py")).toEqual([]);
    });
  });

  describe("Robustness (C36-08)", () => {
    it("returns zero findings on invalid Python syntax", () => {
      const content = readFileSync(join(FIXTURES_DIR, "syntax_error.py"), "utf-8");
      const parsed = parsePythonSource("syntax_error.py", content);
      expect(parsed.hasSyntaxError).toBe(true);
      expect(checkCodeC36(parsed)).toEqual([]);
    });

    it("returns zero findings on an empty file", () => {
      expect(run("")).toEqual([]);
    });
  });

  describe("Fingerprints (C36-09)", () => {
    const src = "def f(lines):\n    rows = [parse(l) for l in lines]\n    for r in rows:\n        if r.ok:\n            return r\n";

    it("are stable across line shifts", () => {
      const before = run(src);
      const after = run("# header\n\n" + src);
      expect(before).toHaveLength(1);
      expect(after[0].fingerprint).toBe(before[0].fingerprint);
      expect(after[0].location.startLine).toBe(before[0].location.startLine + 2);
    });

    it("identical producers in one scope get distinct fingerprints", () => {
      const twice = "def f(xs):\n    a = [g(x) for x in xs][0]\n    b = [g(x) for x in xs][0]\n";
      const [a, b] = run(twice);
      expect(a.fingerprint).not.toBe(b.fingerprint);
    });

    it("`# noqa: CODE-C3.6` on the producer or the consumer line suppresses", () => {
      expect(run(src.replace("for l in lines]", "for l in lines]  # noqa: CODE-C3.6"))).toEqual([]);
      expect(run(src.replace("for r in rows:", "for r in rows:  # noqa: CODE-C3.6"))).toEqual([]);
    });
  });

  describe("Contract v1", () => {
    const read = (f: string) => readFileSync(join(FIXTURES_DIR, f), "utf-8");

    it("positives: completed with line-free identities; syntax error is never certified", () => {
      const result = evaluate(staticInput("CODE-C3.6", { "positives.py": read("positives.py") }));
      expect(result.status).toBe("completed");
      expect(result.findings).toHaveLength(runCheckOnFixture("positives.py").length);
      expect(result.findings.every((f) => /eager-then-/.test(f.identity))).toBe(true);

      const broken = evaluate(staticInput("CODE-C3.6", { "syntax_error.py": read("syntax_error.py") }));
      expect(broken.status).toBe("unavailable");
      expect(broken.findings).toEqual([]);
    });

    it.skipIf(!validatorAvailable)("positives, negatives and syntax-error pairs pass shared validate_pair", () => {
      for (const files of [
        { "positives.py": read("positives.py") },
        { "negatives.py": read("negatives.py") },
        { "positives.py": read("positives.py"), "syntax_error.py": read("syntax_error.py") },
      ]) {
        const input = staticInput("CODE-C3.6", files);
        const verdict = validatePair(input, evaluate(input));
        expect(verdict.ok, verdict.output).toBe(true);
      }
    });
  });
});
