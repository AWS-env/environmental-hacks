import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { parsePythonSource } from "../src/core/parse.js";
import { checkCodeC33 } from "../src/checks/code-c3-3/index.js";
import { checkCodeC32 } from "../src/checks/code-c3-2/index.js";
import { evaluate } from "../src/contract.js";
import { staticInput, validatePair, validatorAvailable } from "./helpers/contract.js";

const FIXTURES_DIR = join(__dirname, "fixtures", "code-c3-3");

function runCheckOnFixture(relPath: string) {
  const content = readFileSync(join(FIXTURES_DIR, relPath), "utf-8");
  const parsed = parsePythonSource(relPath, content);
  expect(parsed.hasSyntaxError).toBe(false);
  return checkCodeC33(parsed);
}

function run(content: string, path = "inline.py") {
  return checkCodeC33(parsePythonSource(path, content));
}

describe("CODE-C3.3 Inefficient per-iteration setup detector", () => {
  describe("Positives fixture (C33-01 … C33-05)", () => {
    const findings = runCheckOnFixture("positives.py");
    const byFactory = (factory: string) =>
      findings.filter((f) => f.evidence.factory === factory);

    it("reports exactly one finding per positive case", () => {
      expect(findings).toHaveLength(10);
      for (const f of findings) {
        expect(f.check).toBe("CODE-C3.3");
        expect(f.kind).toBe("per-iteration-setup");
        expect(f.evidenceTier).toBe("static");
        expect(f.impact.quantified).toBe(false);
        expect(f.fingerprint).toMatch(/^[0-9a-f]{16}$/);
        expect(f.references.map((r) => r.id)).toEqual(["SRC-01", "taxonomy-c3.3"]);
        expect(f.limitations.some((l) => l.includes("safe to share"))).toBe(true);
        expect(f.agentPrompt).toContain("Hoist it before the loop");
      }
    });

    it("S1b re.compile: Low severity, Medium confidence, light tier (re caches compiled patterns)", () => {
      const compiles = byFactory("re.compile");
      expect(compiles).toHaveLength(2);
      expect(compiles[0].severity).toBe("low");
      expect(compiles[0].confidence).toBe("medium");
      expect(compiles[0].evidence.costTier).toBe("light");
      expect(compiles[0].why).toMatch(/caches recently compiled patterns/);
      expect(compiles[0].evidence.symbol).toBe("rx");
      expect(compiles[0].evidence.snippet).toBe("rx = re.compile(rule)");
    });

    it("S1 compile: other compilers keep Medium severity, High confidence, heavy tier", () => {
      const src =
        "import regex\nimport jinja2\nrule = 'x+'\nfor line in lines:\n    a = regex.compile(rule)\n    t = jinja2.Template(rule)\n    c = compile(rule, 'f', 'exec')\n";
      const found = run(src);
      expect(found.map((f) => f.evidence.factory).sort()).toEqual(["compile", "jinja2.Template", "regex.compile"]);
      for (const f of found) {
        expect(f.severity).toBe("medium");
        expect(f.confidence).toBe("high");
        expect(f.evidence.costTier).toBe("heavy");
      }
    });

    it("S1 compile: heavy-tier details", () => {
      const compiles = byFactory("re.compile");
      expect(compiles[0].evidence.symbol).toBe("rx");
      expect(compiles[0].evidence.snippet).toBe("rx = re.compile(rule)");
      // Aliased import resolves to the qualified template class.
      expect(byFactory("jinja2.Template")).toHaveLength(1);
    });

    it("S2 connection: High severity via assignment, `with` item and aliased import", () => {
      const sessions = byFactory("requests.Session");
      expect(sessions).toHaveLength(3);
      for (const s of sessions) {
        expect(s.severity).toBe("high");
        expect(s.confidence).toBe("medium");
        expect(s.agentPrompt).toContain("close it once after");
      }
      expect(sessions.map((s) => s.evidence.symbol)).toEqual(["s", "s", "s"]);
      expect(byFactory("boto3.client")).toHaveLength(1);
    });

    it("S3 read-mode open, including an open() nested in another call", () => {
      const opens = byFactory("open");
      expect(opens).toHaveLength(2);
      for (const o of opens) {
        expect(o.severity).toBe("medium");
        expect(o.confidence).toBe("medium");
      }
    });

    it("S4 Tier B CapWords construction is Low with unknown cost tier", () => {
      const tierB = byFactory("Formatter");
      expect(tierB).toHaveLength(1);
      expect(tierB[0].severity).toBe("low");
      expect(tierB[0].evidence.costTier).toBe("unknown");
    });

    it("attributes setup invariant in nested loops to the outermost loop", () => {
      const nested = findings.find((f) => f.location.startLine === 69);
      expect(nested).toBeDefined();
      expect(nested!.agentPrompt).toContain('loop at line 67 ("for x in xs")');
    });
  });

  describe("Negatives fixture (C33-06 … C33-08)", () => {
    it("reports zero findings where every guard applies", () => {
      expect(runCheckOnFixture("negatives.py")).toEqual([]);
    });
  });

  describe("Boundary with C3.2", () => {
    it("re.compile in a loop is claimed by C3.3 only", () => {
      const src = "import re\nfor line in lines:\n    m = re.compile(rule).search(line)\n";
      const parsed = parsePythonSource("b.py", src);
      expect(checkCodeC33(parsed)).toHaveLength(1);
      expect(checkCodeC32(parsed)).toEqual([]);
    });

    it("an aliased `from re import compile as c` is also C3.3's", () => {
      const src = "from re import compile as c\nfor line in lines:\n    m = c(rule).search(line)\n";
      const parsed = parsePythonSource("b.py", src);
      expect(checkCodeC33(parsed).map((f) => f.evidence.factory)).toEqual(["re.compile"]);
      expect(checkCodeC32(parsed)).toEqual([]);
    });
  });

  describe("Objects that outlive the iteration (audit F4)", () => {
    const run = (src: string) => checkCodeC33(parsePythonSource("a.py", src));
    const factories = (src: string) => run(src).map((f) => f.evidence.factory);

    it("an object appended to an outer list is a new object per item, not hoistable setup (aiohttp Morsel)", () => {
      const src = "def f(items):\n    out = []\n    for i in items:\n        m = Morsel()\n        m.set(i, i, i)\n        out.append(m)\n    return out\n";
      expect(run(src)).toEqual([]);
    });

    it("an object handed to a method of another object is stored there (aiohttp add_subapp)", () => {
      const src = "def f(app, n):\n    for count in range(n):\n        subapp = Application()\n        app.add_subapp(f'/p/{count}', subapp)\n";
      expect(run(src)).toEqual([]);
    });

    it("an object stored into a subscript or attribute escapes", () => {
      expect(run("def f(items, d):\n    for i in items:\n        w = Widget()\n        d[i] = w\n")).toEqual([]);
      expect(run("def f(items, self):\n    for i in items:\n        w = Widget()\n        self.last = w\n")).toEqual([]);
    });

    it("an object yielded or returned from the loop escapes", () => {
      expect(run("def f(items):\n    for i in items:\n        w = Widget()\n        yield w\n")).toEqual([]);
      expect(run("def f(items):\n    for i in items:\n        w = Widget()\n        if i:\n            return w\n")).toEqual([]);
    });

    it("a construction used as a `with` item has a per-iteration lifecycle (fastapi TestClient)", () => {
      const src = "def f(app):\n    for _ in range(2):\n        with TestClient(app) as client:\n            assert client.get('/').status_code == 500\n";
      expect(run(src)).toEqual([]);
    });

    it("still flags a CapWords object that is created, used and dropped inside the iteration (twin)", () => {
      const src = "def f(items, cfg):\n    for i in items:\n        fmt = Formatter(cfg)\n        print(fmt.render(i))\n";
      expect(factories(src)).toEqual(["Formatter"]);
    });

    it("still flags it when the object is only passed to a plain function", () => {
      const src = "def f(items, cfg):\n    for i in items:\n        fmt = Formatter(cfg)\n        show(fmt, i)\n";
      expect(factories(src)).toEqual(["Formatter"]);
    });

    it("heavy setup signals are unaffected by the escape guard", () => {
      const src = "import re\ndef f(lines, rule):\n    for line in lines:\n        m = re.compile(rule)\n        use(m, line)\n";
      expect(factories(src)).toEqual(["re.compile"]);
    });
  });

  describe("Robustness (C33-09)", () => {
    it("returns zero findings on invalid Python syntax", () => {
      const content = readFileSync(join(FIXTURES_DIR, "syntax_error.py"), "utf-8");
      const parsed = parsePythonSource("syntax_error.py", content);
      expect(parsed.hasSyntaxError).toBe(true);
      expect(checkCodeC33(parsed)).toEqual([]);
    });

    it("returns zero findings on an empty file", () => {
      expect(run("")).toEqual([]);
    });
  });

  describe("Fingerprints (C33-10)", () => {
    it("are stable across line shifts and distinct for identical-header loops", () => {
      const before = run("import re\nfor l in ls:\n    rx = re.compile(p)\n");
      const after = run("import re\n# moved\n\nfor l in ls:\n    rx = re.compile(p)\n");
      expect(after[0].fingerprint).toBe(before[0].fingerprint);

      const twin = run(
        "import re\nfor l in ls:\n    rx = re.compile(p)\nfor l in ls:\n    rx = re.compile(p)\n"
      );
      expect(twin).toHaveLength(2);
      expect(twin[0].fingerprint).not.toBe(twin[1].fingerprint);
    });
  });

  describe("Contract v1", () => {
    const read = (f: string) => readFileSync(join(FIXTURES_DIR, f), "utf-8");

    it("positives: completed with line-free identities; syntax error is never certified", () => {
      const result = evaluate(staticInput("CODE-C3.3", { "positives.py": read("positives.py") }));
      expect(result.status).toBe("completed");
      expect(result.findings).toHaveLength(runCheckOnFixture("positives.py").length);
      expect(result.findings.every((f) => f.identity.includes("per-iteration-setup:"))).toBe(true);

      const broken = evaluate(staticInput("CODE-C3.3", { "syntax_error.py": read("syntax_error.py") }));
      expect(broken.status).toBe("unavailable");
      expect(broken.findings).toEqual([]);
    });

    it.skipIf(!validatorAvailable)("positives, negatives and syntax-error pairs pass shared validate_pair", () => {
      for (const files of [
        { "positives.py": read("positives.py") },
        { "negatives.py": read("negatives.py") },
        { "positives.py": read("positives.py"), "syntax_error.py": read("syntax_error.py") },
      ]) {
        const input = staticInput("CODE-C3.3", files);
        const verdict = validatePair(input, evaluate(input));
        expect(verdict.ok, verdict.output).toBe(true);
      }
    });
  });
});
