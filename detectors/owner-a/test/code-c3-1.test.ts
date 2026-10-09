import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { parsePythonSource } from "../src/core/parse.js";
import { checkCodeC31 } from "../src/checks/code-c3-1/index.js";
import { evaluate } from "../src/contract.js";
import { staticInput, validatePair, validatorAvailable } from "./helpers/contract.js";

const FIXTURES_DIR = join(__dirname, "fixtures", "code-c3-1");

function runCheckOnFixture(relPath: string) {
  const fullPath = join(FIXTURES_DIR, relPath);
  const content = readFileSync(fullPath, "utf-8");
  const parsed = parsePythonSource(relPath, content);
  expect(parsed.hasSyntaxError).toBe(false);
  return checkCodeC31(parsed);
}

function runCheckOnSource(relPath: string, content: string) {
  const parsed = parsePythonSource(relPath, content);
  return { parsed, findings: checkCodeC31(parsed) };
}

describe("CODE-C3.1 Inefficient iteration construct detector (static half)", () => {
  describe("Positives fixture", () => {
    it("detects all four signals in positives.py", () => {
      const findings = runCheckOnFixture("positives.py");

      // s1_indexing, s1_control_flow_ok, s2_manual_while, s3_append,
      // s3_gated, s3_annotated, s4_key_lookup, s4_keys_call, nested x2
      expect(findings).toHaveLength(10);

      for (const f of findings) {
        expect(f.check).toBe("CODE-C3.1");
        expect(f.kind).toBe("inefficient-iteration-construct");
        expect(f.severity).toBe("low");
        // Only the bare `for k in d` loop over an untyped parameter is Medium.
        expect(f.confidence).toBe(
          f.evidence.snippet === "for k in d:" ? "medium" : "high"
        );
        expect(f.evidenceTier).toBe("static");
        expect(f.impact.quantified).toBe(false);
        expect(f.fingerprint).toMatch(/^[0-9a-f]{16}$/);
        expect(f.references[0].id).toBe("SRC-01");
        expect(f.agentPrompt.length).toBeGreaterThan(0);
        // Engine caveat + unconfirmed-profiler note on every finding.
        expect(f.limitations.length).toBeGreaterThanOrEqual(2);
        expect(f.evidence.suggested).toBeDefined();
      }

      // S1 range(len()) indexing loop
      const s1 = findings.filter((f) =>
        f.evidence.snippet.includes("range(len(xs))")
      );
      expect(s1.length).toBeGreaterThanOrEqual(1);
      expect(s1[0].evidence.loopType).toBe("for");
      expect(s1[0].evidence.symbol).toBe("xs");
      expect(s1[0].evidence.suggested).toContain("enumerate(xs)");

      // S1 stays valid when the body has control flow (header-only rewrite)
      const s1Flow = findings.find((f) =>
        f.location.startLine ===
        findings.find((g) => g.evidence.snippet.includes("range(len(xs))"))
          ?.location.startLine
      );
      expect(s1Flow).toBeDefined();

      // S2 manual-index while loop
      const s2 = findings.find((f) => f.evidence.loopType === "while");
      expect(s2).toBeDefined();
      expect(s2?.evidence.symbol).toBe("xs");
      expect(s2?.evidence.suggested).toContain("enumerate(xs)");

      // S3 ungated append accumulation
      const s3 = findings.find(
        (f) =>
          f.evidence.symbol === "out" &&
          f.evidence.suggested === "[<expr> for x in xs]"
      );
      expect(s3).toBeDefined();

      // S3 gated append accumulation
      const s3g = findings.find(
        (f) =>
          f.evidence.symbol === "out" &&
          f.evidence.suggested === "[<expr> for x in xs if <cond>]"
      );
      expect(s3g).toBeDefined();

      // S4 dict key loops (plain and .keys())
      const s4 = findings.filter((f) =>
        f.evidence.suggested?.includes(".items()")
      );
      expect(s4).toHaveLength(2);
      for (const f of s4) expect(f.evidence.symbol).toBe("d");

      // Nested loops attribute one finding to each loop that owns the form
      const nested = findings.filter((f) =>
        f.evidence.snippet.includes("range(len(xss")
      );
      expect(nested).toHaveLength(2);
    });
  });

  describe("Negatives fixture", () => {
    it("reports zero findings on negatives.py where every guard applies", () => {
      const findings = runCheckOnFixture("negatives.py");
      expect(findings).toEqual([]);
    });
  });

  describe("Precedence", () => {
    it("reports an append-accumulating index loop once, as a comprehension", () => {
      const { parsed, findings } = runCheckOnSource(
        "precedence.py",
        "out = []\nfor i in range(len(xs)):\n    out.append(xs[i])\n"
      );
      expect(parsed.hasSyntaxError).toBe(false);
      expect(findings).toHaveLength(1);
      expect(findings[0].evidence.suggested).toContain("for i in range(len(xs))");
    });
  });

  describe("S2 increment variants", () => {
    it("accepts `i = i + 1` as the manual increment", () => {
      const { findings } = runCheckOnSource(
        "incr.py",
        "i = 0\nwhile i < len(xs):\n    print(xs[i])\n    i = i + 1\n"
      );
      expect(findings).toHaveLength(1);
      expect(findings[0].evidence.loopType).toBe("while");
    });
  });

  describe("Audit regressions (2026-10-09)", () => {
    it("S4: never suggests .items() for a list iterated by its own values", () => {
      const { findings } = runCheckOnSource(
        "perm.py",
        "perm = [2, 0, 1]\nfor i in perm:\n    out = perm[i]\n"
      );
      expect(findings).toEqual([]);
    });

    it("S4: dict-bound name is High, untyped name is Medium with a caveat", () => {
      const typed = runCheckOnSource(
        "d.py",
        "d = {}\nfor k in d:\n    print(k, d[k])\n"
      ).findings;
      expect(typed).toHaveLength(1);
      expect(typed[0].confidence).toBe("high");

      const annotated = runCheckOnSource(
        "a.py",
        "def f(d: dict[str, int]):\n    for k in d:\n        print(k, d[k])\n"
      ).findings;
      expect(annotated[0].confidence).toBe("high");

      const untyped = runCheckOnSource(
        "u.py",
        "def f(d):\n    for k in d:\n        print(k, d[k])\n"
      ).findings;
      expect(untyped[0].confidence).toBe("medium");
      expect(untyped[0].limitations.some((l) => l.includes("not provably a dict"))).toBe(true);
    });

    it("S2: a conditional increment is not a plain traversal", () => {
      const { findings } = runCheckOnSource(
        "cond.py",
        "i = 0\nwhile i < len(xs):\n    if xs[i] == sep:\n        i += 1\n    else:\n        handle(xs[i])\n"
      );
      expect(findings).toEqual([]);
    });

    it("S2: `continue` in the body blocks the rewrite", () => {
      const { findings } = runCheckOnSource(
        "cont.py",
        "i = 0\nwhile i < len(xs):\n    if skip(xs[i]):\n        continue\n    use(xs[i])\n    i += 1\n"
      );
      expect(findings).toEqual([]);
    });

    it("S2: a `continue` inside a nested loop does not block it", () => {
      const { findings } = runCheckOnSource(
        "nested_cont.py",
        "i = 0\nwhile i < len(xs):\n    for y in ys:\n        if y:\n            continue\n    use(xs[i])\n    i += 1\n"
      );
      expect(findings).toHaveLength(1);
    });

    it("S3: `# noqa: CODE-C3.1` on the append line suppresses", () => {
      const { findings } = runCheckOnSource(
        "noqa.py",
        "out = []\nfor x in xs:\n    out.append(f(x))  # noqa: CODE-C3.1\n"
      );
      expect(findings).toEqual([]);
    });

    it("S3: `async for` suggests an async comprehension", () => {
      const { findings } = runCheckOnSource(
        "async.py",
        "async def g(xs):\n    out = []\n    async for x in xs:\n        out.append(x)\n    return out\n"
      );
      expect(findings).toHaveLength(1);
      expect(findings[0].evidence.suggested).toBe("[<expr> async for x in xs]");
    });
  });

  describe("Robustness", () => {
    it("gracefully returns zero findings on invalid Python syntax", () => {
      const fullPath = join(FIXTURES_DIR, "syntax_error.py");
      const content = readFileSync(fullPath, "utf-8");
      const parsed = parsePythonSource("syntax_error.py", content);
      expect(parsed.hasSyntaxError).toBe(true);
      expect(checkCodeC31(parsed)).toEqual([]);
    });

    it("returns zero findings on an empty file", () => {
      const { parsed, findings } = runCheckOnSource("empty.py", "");
      expect(parsed.hasSyntaxError).toBe(false);
      expect(findings).toEqual([]);
    });
  });

  describe("Deterministic fingerprints", () => {
    it("generates consistent 16-char hex fingerprints for identical signals", () => {
      const code = "for i in range(len(xs)):\n    print(xs[i])\n";
      const first = runCheckOnSource("app.py", code).findings;
      const second = runCheckOnSource("app.py", code).findings;
      expect(first).toHaveLength(1);
      expect(first[0].fingerprint).toHaveLength(16);
      expect(first[0].fingerprint).toBe(second[0].fingerprint);
    });

    it("keeps the fingerprint stable when edits above the loop shift line numbers", () => {
      const before = runCheckOnSource(
        "app.py",
        "for i in range(len(xs)):\n    print(xs[i])\n"
      ).findings;
      const after = runCheckOnSource(
        "app.py",
        "# comment added above\n\nfor i in range(len(xs)):\n    print(xs[i])\n"
      ).findings;
      expect(after).toHaveLength(1);
      expect(after[0].fingerprint).toBe(before[0].fingerprint);
      expect(after[0].location.startLine).not.toBe(
        before[0].location.startLine
      );
    });
  });

  describe("Contract v1 (C31-12)", () => {
    const read = (f: string) => readFileSync(join(FIXTURES_DIR, f), "utf-8");

    it("positives: completed, one contract finding per owner-a finding, line-free identities", () => {
      const input = staticInput("CODE-C3.1", { "positives.py": read("positives.py") });
      const result = evaluate(input);
      expect(result.status).toBe("completed");
      expect(result.findings).toHaveLength(10);
      expect(result.findings.every((f) => f.identity.includes("inefficient-iteration-construct:"))).toBe(true);
      expect(result.coverage.limitations.some((l) => l.includes("R1R2"))).toBe(true);
    });

    it("syntax error is never certified", () => {
      const result = evaluate(staticInput("CODE-C3.1", { "syntax_error.py": read("syntax_error.py") }));
      expect(result.status).toBe("unavailable");
      expect(result.findings).toEqual([]);
    });

    it.skipIf(!validatorAvailable)("positives, negatives and syntax-error pairs pass shared validate_pair", () => {
      for (const files of [
        { "positives.py": read("positives.py") },
        { "negatives.py": read("negatives.py") },
        { "positives.py": read("positives.py"), "syntax_error.py": read("syntax_error.py") },
      ]) {
        const input = staticInput("CODE-C3.1", files);
        const verdict = validatePair(input, evaluate(input));
        expect(verdict.ok, verdict.output).toBe(true);
      }
    });
  });
});
