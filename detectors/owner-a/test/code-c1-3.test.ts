import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { parsePythonSource } from "../src/core/parse.js";
import { checkCodeC13 } from "../src/checks/code-c1-3/index.js";
import { evaluate } from "../src/contract.js";
import { staticInput, validatePair, validatorAvailable } from "./helpers/contract.js";

const FIXTURES_DIR = join(__dirname, "fixtures", "code-c1-3");

function runCheckOnFixture(relPath: string) {
  const content = readFileSync(join(FIXTURES_DIR, relPath), "utf-8");
  const parsed = parsePythonSource(relPath, content);
  expect(parsed.hasSyntaxError).toBe(false);
  return checkCodeC13(parsed);
}

function run(content: string, path = "inline.py") {
  return checkCodeC13(parsePythonSource(path, content));
}

describe("CODE-C1.3 Redundant control flow detector", () => {
  describe("Positives fixture (C13-01 … C13-04)", () => {
    const findings = runCheckOnFixture("positives.py");
    const bySnippet = (s: string) => findings.find((f) => f.evidence.snippet === s);

    it("reports exactly one finding per positive construct", () => {
      expect(findings).toHaveLength(7);
      for (const f of findings) {
        expect(f.check).toBe("CODE-C1.3");
        expect(f.evidenceTier).toBe("static");
        expect(f.impact.quantified).toBe(false);
        expect(f.fingerprint).toMatch(/^[0-9a-f]{16}$/);
        expect(f.references.map((r) => r.id)).toEqual(["SRC-01", "taxonomy-c1.3"]);
        expect(f.limitations.some((l) => l.includes("hot loop"))).toBe(true);
      }
      expect(findings.filter((f) => f.kind === "identical-branches")).toHaveLength(4);
      expect(findings.filter((f) => f.kind === "empty-branch")).toHaveLength(3);
    });

    it("S1 identical if/else with a pure condition: Low/High, spans the statement", () => {
      const f = bySnippet("if x > 0:")!;
      expect(f.kind).toBe("identical-branches");
      expect(f.severity).toBe("low");
      expect(f.confidence).toBe("high");
      expect(f.evidence.expr).toBe("x > 0");
      expect(f.location.endLine).toBe(f.location.startLine + 3);
      expect(f.agentPrompt).toContain("collapse `if x > 0:` into its shared body");
      expect(f.agentPrompt).not.toContain("bare statement");
    });

    it("S1 elif chain ignores comments and lists every condition", () => {
      const f = bySnippet('if mode == "a":')!;
      expect(f.why).toContain("All 3 arms");
      expect(f.evidence.expr).toBe('mode == "a", mode == "b"');
    });

    it("S1 call condition in a loop: Medium/Medium, keep-the-condition caveat, loop noted", () => {
      const f = bySnippet("if is_valid(row):")!;
      expect(f.severity).toBe("medium");
      expect(f.confidence).toBe("medium");
      expect(f.evidence.loopType).toBe("for");
      expect(f.why).toContain("enclosing for loop");
      expect(f.limitations.some((l) => l.includes("bare statement"))).toBe(true);
      expect(f.agentPrompt).toContain("keep `is_valid(row)` as a bare statement");
    });

    it("S1 identical ternary arms: lookup condition is Low/Medium", () => {
      const f = bySnippet("return default if cfg.strict else default")!;
      expect(f.kind).toBe("identical-branches");
      expect(f.severity).toBe("low");
      expect(f.confidence).toBe("medium");
      expect(f.agentPrompt).toContain("with `default`");
    });

    it("S2 all-empty if (pass or ...): Low/High, remove the statement", () => {
      for (const snippet of ["if flag:", "if x is None:"]) {
        const f = bySnippet(snippet);
        expect(f, snippet).toBeDefined();
        expect(f!.kind).toBe("empty-branch");
        expect(f!.confidence).toBe("high");
        expect(f!.agentPrompt).toContain(`remove the empty \`${snippet}\` statement`);
      }
    });

    it("S2 trailing empty elif: spans from the elif to the end of the statement", () => {
      const f = bySnippet("elif n == 0:")!;
      expect(f.kind).toBe("empty-branch");
      expect(f.evidence.expr).toBe("n == 0");
      expect(f.location.endLine).toBe(f.location.startLine + 1);
      expect(f.agentPrompt).toContain("delete the empty trailing arm starting at `elif n == 0:`");
    });
  });

  describe("Negatives fixture (C13-05 … C13-07)", () => {
    it("reports zero findings where the branch taken changes what runs", () => {
      expect(runCheckOnFixture("negatives.py")).toEqual([]);
    });
  });

  describe("Boundaries (C13-08)", () => {
    it("`if c: pass else: pass` is reported once, as an empty branch", () => {
      const f = run("def f(c):\n    if c:\n        pass\n    else:\n        pass\n");
      expect(f.map((x) => x.kind)).toEqual(["empty-branch"]);
    });

    it("several trailing empty elifs are one finding listing each condition", () => {
      const [f, ...rest] = run(
        "def f(a, b, c, g):\n    if a:\n        g()\n    elif b:\n        pass\n    elif c:\n        ...\n    else:\n        pass\n"
      );
      expect(rest).toEqual([]);
      expect(f.evidence.expr).toBe("b, c");
      expect(f.why).toContain("arms are empty");
    });

    it("nested redundancy inside a collapsible branch is reported separately", () => {
      const f = run(
        "def f(a, b, g):\n    if a:\n        if b:\n            g()\n        else:\n            g()\n    else:\n        g()\n"
      );
      expect(f.map((x) => x.evidence.snippet)).toEqual(["if b:"]);
    });

    it("a walrus condition is Low/Medium: collapsing must keep the binding", () => {
      const [f] = run("def f(g):\n    if (m := g):\n        use(m)\n    else:\n        use(m)\n");
      expect(f.severity).toBe("low");
      expect(f.confidence).toBe("medium");
    });

    it("a ternary inside a comprehension is noted as a for loop", () => {
      const [f] = run("def f(xs, d):\n    return [d if x else d for x in xs]\n");
      expect(f.evidence.loopType).toBe("for");
    });

    it("module-level code is analysed too", () => {
      expect(run("if DEBUG:\n    pass\n")).toHaveLength(1);
    });
  });

  describe("String literals with escape sequences (audit F1)", () => {
    // tree-sitter gives a string with escapes `string_content` children that are only the escapes,
    // so the plain text between them must still take part in the comparison.
    const arms = (a: string, b: string) => `def f(x):\n    if x:\n        print(${a})\n    else:\n        print(${b})\n`;

    it("arms that differ only in text next to an escape are not identical", () => {
      expect(run(arms(String.raw`"a\n"`, String.raw`"b\n"`))).toEqual([]);
    });

    it("the rich traceback shape (long messages, only escapes in common) is not identical", () => {
      const src = String.raw`def f(stack, last):
    if not last:
        if stack.is_cause:
            yield Text.from_markup(
                "\n[i]The above exception was the direct cause of the following exception:\n",
            )
        else:
            yield Text.from_markup(
                "\n[i]During handling of the above exception, another exception occurred:\n",
            )
`;
      expect(run(src)).toEqual([]);
    });

    it("a ternary whose two values differ only around an escape is not identical", () => {
      expect(run(String.raw`def f(x):
    return "a\n" if x else "b\n"
`)).toEqual([]);
    });

    it("f-strings with escapes compare their literal text too", () => {
      expect(run(String.raw`def f(x, n):
    if x:
        print(f"a\n{n}")
    else:
        print(f"b\n{n}")
`)).toEqual([]);
    });

    it("arms with the same escaped string are still identical", () => {
      const f = run(arms(String.raw`"a\n"`, String.raw`"a\n"`));
      expect(f.map((x) => x.kind)).toEqual(["identical-branches"]);
    });

    it("arms with the same text and different quoting of an escape-free string stay unflagged", () => {
      expect(run(arms(`"a"`, `"b"`))).toEqual([]);
    });
  });

  describe("Robustness (C13-09)", () => {
    it("returns zero findings on invalid Python syntax", () => {
      const content = readFileSync(join(FIXTURES_DIR, "syntax_error.py"), "utf-8");
      const parsed = parsePythonSource("syntax_error.py", content);
      expect(parsed.hasSyntaxError).toBe(true);
      expect(checkCodeC13(parsed)).toEqual([]);
    });

    it("returns zero findings on an empty file", () => {
      expect(run("")).toEqual([]);
    });
  });

  describe("Fingerprints (C13-10)", () => {
    const branch = "def f(x, g):\n    if x:\n        g(x)\n    else:\n        g(x)\n";

    it("are stable across line shifts", () => {
      const before = run(branch);
      const after = run("# header\n\n" + branch);
      expect(before).toHaveLength(1);
      expect(after[0].fingerprint).toBe(before[0].fingerprint);
      expect(after[0].identity).toBe(before[0].identity);
    });

    it("differ for identical branches in different functions", () => {
      const twice = run(branch + "\n\n" + branch.replace("def f", "def h"));
      expect(twice).toHaveLength(2);
      expect(twice[0].fingerprint).not.toBe(twice[1].fingerprint);
    });

    it("`# noqa: CODE-C1.3` on any arm header suppresses", () => {
      expect(run(branch.replace("else:", "else:  # noqa: CODE-C1.3"))).toEqual([]);
      const elif = "def f(a, b, g):\n    if a:\n        g()\n    elif b:  # noqa: CODE-C1.3\n        pass\n";
      expect(run(elif)).toEqual([]);
    });

    it("another check's noqa code does not suppress", () => {
      expect(run(branch.replace("if x:", "if x:  # noqa: CODE-C1.2"))).toHaveLength(1);
    });
  });

  describe("Contract v1", () => {
    const read = (f: string) => readFileSync(join(FIXTURES_DIR, f), "utf-8");

    it("positives: completed with line-free identities; syntax error is never certified", () => {
      const result = evaluate(staticInput("CODE-C1.3", { "positives.py": read("positives.py") }));
      expect(result.status).toBe("completed");
      expect(result.findings).toHaveLength(runCheckOnFixture("positives.py").length);
      expect(result.findings.every((f) => /::(identical-branches|empty-branch):/.test(f.identity))).toBe(true);

      const broken = evaluate(staticInput("CODE-C1.3", { "syntax_error.py": read("syntax_error.py") }));
      expect(broken.status).toBe("unavailable");
      expect(broken.findings).toEqual([]);
    });

    it.skipIf(!validatorAvailable)("positives, negatives and syntax-error pairs pass shared validate_pair", () => {
      for (const files of [
        { "positives.py": read("positives.py") },
        { "negatives.py": read("negatives.py") },
        { "positives.py": read("positives.py"), "syntax_error.py": read("syntax_error.py") },
      ]) {
        const input = staticInput("CODE-C1.3", files);
        const verdict = validatePair(input, evaluate(input));
        expect(verdict.ok, verdict.output).toBe(true);
      }
    });
  });
});
