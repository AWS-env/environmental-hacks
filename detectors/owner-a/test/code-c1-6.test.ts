import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { parsePythonSource } from "../src/core/parse.js";
import { checkCodeC16 } from "../src/checks/code-c1-6/index.js";
import { evaluate } from "../src/contract.js";
import { staticInput, validatePair, validatorAvailable } from "./helpers/contract.js";

const FIXTURES_DIR = join(__dirname, "fixtures", "code-c1-6");

function runCheckOnFixture(relPath: string) {
  const content = readFileSync(join(FIXTURES_DIR, relPath), "utf-8");
  const parsed = parsePythonSource(relPath, content);
  expect(parsed.hasSyntaxError).toBe(false);
  return checkCodeC16(parsed);
}

function run(content: string, path = "inline.py") {
  return checkCodeC16(parsePythonSource(path, content));
}

describe("CODE-C1.6 Unnecessary initialization detector", () => {
  describe("Positives fixture (C16-01 … C16-04)", () => {
    const findings = runCheckOnFixture("positives.py");
    const bySnippet = (s: string) => findings.find((f) => f.evidence.snippet === s);

    it("reports exactly one finding per positive construct", () => {
      expect(findings).toHaveLength(6);
      for (const f of findings) {
        expect(f.check).toBe("CODE-C1.6");
        expect(f.evidenceTier).toBe("static");
        expect(f.impact.quantified).toBe(false);
        expect(f.fingerprint).toMatch(/^[0-9a-f]{16}$/);
        expect(f.references.map((r) => r.id)).toEqual(["SRC-01", "taxonomy-c1.6"]);
        expect(f.limitations.some((l) => l.includes("not measured"))).toBe(true);
      }
      expect(findings.filter((f) => f.kind === "overwritten-init")).toHaveLength(2);
      expect(findings.filter((f) => f.kind === "init-before-early-exit")).toHaveLength(4);
    });

    it("S1 allocation overwritten in every arm: Low/High, spans setup to the if chain", () => {
      const f = bySnippet("rows = []")!;
      expect(f.kind).toBe("overwritten-init");
      expect(f.severity).toBe("low");
      expect(f.confidence).toBe("high");
      expect(f.evidence.symbol).toBe("rows");
      expect(f.evidence.expr).toBe("[]");
      expect(f.location.endLine).toBe(f.location.startLine + 4);
      expect(f.why).toContain("assigns it again before reading it");
      expect(f.agentPrompt).toContain("delete the initial `rows = []`");
      expect(f.agentPrompt).not.toContain("bare statement");
    });

    it("S1 call with an arm that raises: Medium/Medium, keep-the-call caveat", () => {
      const f = bySnippet("data = parse(path)")!;
      expect(f.kind).toBe("overwritten-init");
      expect(f.severity).toBe("medium");
      expect(f.confidence).toBe("medium");
      expect(f.why).toContain("assigns it again or leaves");
      expect(f.limitations.some((l) => l.includes("bare statement"))).toBe(true);
      expect(f.agentPrompt).toContain("keep `parse(path)` as a bare statement");
    });

    it("S2 file open before a guard: High/Medium with the Tier A factory", () => {
      const f = bySnippet("fh = open(path)")!;
      expect(f.kind).toBe("init-before-early-exit");
      expect(f.severity).toBe("high");
      expect(f.confidence).toBe("medium");
      expect(f.evidence.factory).toBe("open");
      expect(f.why).toContain("opens a file (`open`)");
      expect(f.limitations.some((l) => l.includes("below the guard"))).toBe(true);
      expect(f.agentPrompt).toContain("move `fh = open(path)` below the guard so it runs only");
    });

    it("S2 resolves the factory through imports", () => {
      const f = bySnippet("session = requests.Session()")!;
      expect(f.evidence.factory).toBe("requests.Session");
      expect(f.why).toContain("opens a connection");
    });

    it("S2 several guards are one finding that spans the first guard", () => {
      const f = bySnippet("seen = set()")!;
      expect(f.severity).toBe("low");
      expect(f.confidence).toBe("high");
      expect(f.why).toContain("(and 1 more)");
      expect(f.location.endLine).toBe(f.location.startLine + 2);
      expect(f.agentPrompt).toContain("below the guards");
    });

    it("S2 `continue` guard in a loop: loop noted", () => {
      const f = bySnippet('parts = r.split(",")')!;
      expect(f.kind).toBe("init-before-early-exit");
      expect(f.evidence.loopType).toBe("for");
      expect(f.why).toContain("inside the enclosing for loop");
    });
  });

  describe("Negatives fixture (C16-05 … C16-07)", () => {
    it("reports zero findings where the setup is read, free, or unsafe to change", () => {
      expect(runCheckOnFixture("negatives.py")).toEqual([]);
    });
  });

  describe("Boundaries (C16-08)", () => {
    it("guards followed by a full overwrite are reported once, as S1", () => {
      const f = run(
        "def f(c, d):\n    x = load()\n    if d:\n        return None\n    if c:\n        x = a()\n    else:\n        x = b()\n    return x\n"
      );
      expect(f.map((x) => x.kind)).toEqual(["overwritten-init"]);
    });

    it("`break` is safe when every mention sits after the setup in its block", () => {
      const ok = "def f(rows):\n    for r in rows:\n        buf = [r]\n        if r is None:\n            break\n        emit(buf)\n";
      expect(run(ok).map((x) => x.kind)).toEqual(["init-before-early-exit"]);
      const read = ok + "    return buf\n";
      expect(run(read)).toEqual([]);
    });

    it("a statement that mentions the setup's inputs blocks the move (conservative: it may rebind them)", () => {
      const f = run("def f(items):\n    view = build(items)\n    log(items)\n    if not items:\n        return\n    return view\n");
      expect(f).toEqual([]);
    });

    it("an empty display allocates; a plain name or constant does not", () => {
      expect(run("def f(c, y):\n    x = {}\n    if c:\n        return\n    return x\n")).toHaveLength(1);
      expect(run("def f(c, y):\n    x = y\n    if c:\n        return\n    return x\n")).toEqual([]);
      expect(run('def f(c):\n    x = "s"\n    if c:\n        return\n    return x\n')).toEqual([]);
    });

    it("an async setup is a call (Medium)", () => {
      const [f] = run("async def f(c):\n    x = await fetch()\n    if c:\n        return\n    return x\n");
      expect(f.severity).toBe("medium");
    });
  });

  describe("Robustness (C16-09)", () => {
    it("returns zero findings on invalid Python syntax", () => {
      const content = readFileSync(join(FIXTURES_DIR, "syntax_error.py"), "utf-8");
      const parsed = parsePythonSource("syntax_error.py", content);
      expect(parsed.hasSyntaxError).toBe(true);
      expect(checkCodeC16(parsed)).toEqual([]);
    });

    it("returns zero findings on an empty file", () => {
      expect(run("")).toEqual([]);
    });
  });

  describe("Fingerprints (C16-10)", () => {
    const guarded = "def f(c):\n    rows = []\n    if c:\n        return None\n    return rows\n";

    it("are stable across line shifts", () => {
      const before = run(guarded);
      const after = run("# header\n\n" + guarded);
      expect(before).toHaveLength(1);
      expect(after[0].fingerprint).toBe(before[0].fingerprint);
      expect(after[0].identity).toBe(before[0].identity);
    });

    it("differ for the same setup in different functions", () => {
      const twice = run(guarded + "\n\n" + guarded.replace("def f", "def h"));
      expect(twice).toHaveLength(2);
      expect(twice[0].fingerprint).not.toBe(twice[1].fingerprint);
    });

    it("`# noqa: CODE-C1.6` on the setup or the guard suppresses", () => {
      expect(run(guarded.replace("rows = []", "rows = []  # noqa: CODE-C1.6"))).toEqual([]);
      expect(run(guarded.replace("if c:", "if c:  # noqa: CODE-C1.6"))).toEqual([]);
    });

    it("another check's noqa code does not suppress", () => {
      expect(run(guarded.replace("rows = []", "rows = []  # noqa: CODE-C1.2"))).toHaveLength(1);
    });
  });

  describe("Contract v1", () => {
    const read = (f: string) => readFileSync(join(FIXTURES_DIR, f), "utf-8");

    it("positives: completed with line-free identities; syntax error is never certified", () => {
      const result = evaluate(staticInput("CODE-C1.6", { "positives.py": read("positives.py") }));
      expect(result.status).toBe("completed");
      expect(result.findings).toHaveLength(runCheckOnFixture("positives.py").length);
      expect(result.findings.every((f) => /::(overwritten-init|init-before-early-exit):/.test(f.identity))).toBe(true);

      const broken = evaluate(staticInput("CODE-C1.6", { "syntax_error.py": read("syntax_error.py") }));
      expect(broken.status).toBe("unavailable");
      expect(broken.findings).toEqual([]);
    });

    it.skipIf(!validatorAvailable)("positives, negatives and syntax-error pairs pass shared validate_pair", () => {
      for (const files of [
        { "positives.py": read("positives.py") },
        { "negatives.py": read("negatives.py") },
        { "positives.py": read("positives.py"), "syntax_error.py": read("syntax_error.py") },
      ]) {
        const input = staticInput("CODE-C1.6", files);
        const verdict = validatePair(input, evaluate(input));
        expect(verdict.ok, verdict.output).toBe(true);
      }
    });
  });
});
