import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { parsePythonSource } from "../src/core/parse.js";
import { checkCodeC104 } from "../src/checks/code-c10-4/index.js";
import { evaluate } from "../src/contract.js";
import { staticInput, validatePair, validatorAvailable } from "./helpers/contract.js";

const FIXTURES_DIR = join(__dirname, "fixtures", "code-c10-4");

function runCheckOnFixture(relPath: string) {
  const content = readFileSync(join(FIXTURES_DIR, relPath), "utf-8");
  const parsed = parsePythonSource(relPath, content);
  expect(parsed.hasSyntaxError).toBe(false);
  return checkCodeC104(parsed);
}

function run(content: string, path = "inline.py") {
  return checkCodeC104(parsePythonSource(path, content));
}

const LOCAL = 'def f(rows):\n    s = ""\n    for r in rows:\n        s += r\n    return s\n';

describe("CODE-C10.4 Inefficient string concatenation detector", () => {
  describe("Positives fixture", () => {
    const findings = runCheckOnFixture("positives.py");
    const at = (qual: string, name: string) =>
      findings.filter((f) => f.identity === `string-concat-in-loop:${qual}:${name}:0`);

    it("reports one finding per accumulating statement with the shared metadata", () => {
      expect(findings).toHaveLength(9);
      for (const f of findings) {
        expect(f.check).toBe("CODE-C10.4");
        expect(f.kind).toBe("string-concat-in-loop");
        expect(f.severity).toBe("low");
        expect(f.evidenceTier).toBe("static");
        expect(f.impact.quantified).toBe(false);
        expect(f.detector.version).toBe("0.1.0");
        expect(f.fingerprint).toMatch(/^[0-9a-f]{16}$/);
        expect(f.references.map((r) => r.id).slice(0, 2)).toEqual(["SRC-01", "taxonomy-c10.4"]);
        expect(f.limitations.some((l) => l.includes("trip count is not measured"))).toBe(true);
        expect(f.agentPrompt).toContain('"".join(parts)');
      }
    });

    it("local name targets are low confidence and carry the UNVERIFIED CPython caveat", () => {
      for (const [qual, name] of [
        ["local_augmented", "out"],
        ["local_self_concat", "s"],
        ["fstring_append", "text"],
        ["while_loop", "buf"],
        ["param_annotated", "prefix"],
        ["joined_start", "s"],
      ]) {
        const [f] = at(qual, name);
        expect(f, `${qual}:${name}`).toBeDefined();
        expect(f.confidence).toBe("low");
        expect(f.limitations.some((l) => l.includes("UNVERIFIED"))).toBe(true);
      }
      expect(at("while_loop", "buf")[0].evidence.loopType).toBe("while");
      expect(at("local_self_concat", "s")[0].evidence.mutator).toBe("= +");
      expect(at("local_augmented", "out")[0].evidence.mutator).toBe("+=");
    });

    it("attribute targets are medium confidence (visible binding, or a clearly-string expression)", () => {
      const [a] = at("Builder.add_all", "self.buf");
      expect(a.confidence).toBe("medium");
      expect(a.limitations.some((l) => l.includes("UNVERIFIED"))).toBe(false);
      const [b] = at("attribute_unknown_binding", "obj.log");
      expect(b.confidence).toBe("medium");
    });

    it("the append-only twin of the read-in-loop cases is still flagged", () => {
      expect(at("append_only_twin", "display")).toHaveLength(1);
    });
  });

  describe("Negatives fixture", () => {
    it("reports zero findings where every guard applies", () => {
      expect(runCheckOnFixture("negatives.py")).toEqual([]);
    });
  });

  describe("Similar negatives: the unguarded twin fires", () => {
    it("baseline fires", () => {
      expect(run(LOCAL)).toHaveLength(1);
    });
    it("numeric accumulation does not", () => {
      expect(run(LOCAL.replace('s = ""', "s = 0").replace("s += r", "s += 1"))).toEqual([]);
      expect(run(LOCAL.replace('s = ""', "s = 0.0"))).toEqual([]);
    });
    it("list / bytes / tuple concatenation does not", () => {
      expect(run(LOCAL.replace('s = ""', "s = []"))).toEqual([]);
      expect(run(LOCAL.replace('s = ""', 's = b""'))).toEqual([]);
      expect(run(LOCAL.replace('s = ""', "s = ()"))).toEqual([]);
      expect(run(LOCAL.replace("s += r", "s += [r]"))).toEqual([]);
    });
    it("a break/return right after the statement does not", () => {
      expect(run(LOCAL.replace("s += r", "s += r\n        break"))).toEqual([]);
      expect(run(LOCAL.replace("s += r", "s += r\n        return s"))).toEqual([]);
    });
    it("a break owned by a nested loop does not guard the outer accumulation", () => {
      const src = LOCAL.replace("s += r", "s += r\n        for y in r:\n            break");
      expect(run(src)).toHaveLength(1);
    });
    it("a nested loop that repeats once still accumulates through the outer loop", () => {
      const src = 'def f(rows):\n    s = ""\n    for r in rows:\n        for y in [1]:\n            s += r\n';
      expect(run(src)).toHaveLength(1);
    });
    it("a reset inside the loop does not (no accumulation across iterations)", () => {
      const src = 'def f(rows):\n    for r in rows:\n        s = ""\n        s += r\n';
      expect(run(src)).toEqual([]);
      // binding between outer and inner loop counts as visible
      const nested = 'def f(rows):\n    for r in rows:\n        s = ""\n        for c in r:\n            s += c\n';
      expect(run(nested)).toHaveLength(1);
    });
    it("`join` / append building does not", () => {
      expect(run('def f(rows):\n    p = []\n    for r in rows:\n        p.append(r)\n    return "".join(p)\n')).toEqual([]);
    });
    it("does not cross into a nested def/lambda/class", () => {
      const src = 'def f(rows):\n    s = ""\n    for r in rows:\n        def g(x):\n            s = ""\n            s += x\n        g(r)\n';
      expect(run(src)).toEqual([]);
      expect(run('s = ""\nfor r in rows:\n    k = lambda: 1\n')).toEqual([]);
    });
    it("is not triggered by a += outside any loop", () => {
      expect(run('def f(a):\n    s = ""\n    s += a\n    return s\n')).toEqual([]);
    });
  });

  describe("Missing / unknown binding", () => {
    it("local with unknown binding is not flagged, even with a string expression", () => {
      expect(run('def f(base, rows):\n    s = base\n    for r in rows:\n        s += "x"\n')).toEqual([]);
      expect(run('def f(s, rows):\n    for r in rows:\n        s += "x"\n')).toEqual([]);
      expect(run('def f(rows):\n    s = g()\n    for r in rows:\n        s += f"{r}"\n')).toEqual([]);
    });
    it("a parameter annotated other than str is not a string binding", () => {
      expect(run("def f(s: int, rows):\n    for r in rows:\n        s += r\n")).toEqual([]);
    });
    it("attribute with unknown binding and unknown expression is not flagged", () => {
      expect(run("def f(o, rows):\n    for r in rows:\n        o.buf += r\n")).toEqual([]);
    });
    it("attribute with unknown binding is flagged only when the expression is clearly a string", () => {
      for (const expr of ['"x"', 'f"{r}"', "str(r)", '"{}".format(r)', '"%s" % r']) {
        expect(run(`def f(o, rows):\n    for r in rows:\n        o.buf += ${expr}\n`), expr).toHaveLength(1);
      }
    });
    it("attribute bound to a number or list is not flagged", () => {
      expect(run("class C:\n    def __init__(self):\n        self.n = 0\n    def f(self, rows):\n        for r in rows:\n            self.n += r\n")).toEqual([]);
      expect(run("class C:\n    def __init__(self):\n        self.n = []\n    def f(self, rows):\n        for r in rows:\n            self.n += r\n")).toEqual([]);
    });
    it("module-level / global string accumulators are medium confidence", () => {
      const mod = run('s = ""\nfor r in rows:\n    s += r\n');
      expect(mod).toHaveLength(1);
      expect(mod[0].confidence).toBe("medium");
      const glob = run('s = ""\n\ndef f(rows):\n    global s\n    for r in rows:\n        s += r\n');
      expect(glob).toHaveLength(1);
      expect(glob[0].confidence).toBe("medium");
    });
  });

  describe("Boundary: constant iterables of 8 vs 9 items", () => {
    const body = (iter: string) => `def f():\n    s = ""\n    for p in ${iter}:\n        s += p\n`;
    it("8 constant items is skipped, 9 is flagged", () => {
      expect(run(body("['a','b','c','d','e','f','g','h']"))).toEqual([]);
      expect(run(body("['a','b','c','d','e','f','g','h','i']"))).toHaveLength(1);
      expect(run(body("(1, 2, 3, 4, 5, 6, 7, 8)"))).toEqual([]);
      expect(run(body("(1, 2, 3, 4, 5, 6, 7, 8, 9)"))).toHaveLength(1);
    });
    it("range of literals: 8 skipped, 9 flagged; non-literal range flagged", () => {
      expect(run(body("range(8)"))).toEqual([]);
      expect(run(body("range(9)"))).toHaveLength(1);
      expect(run(body("range(2, 10)"))).toEqual([]);
      expect(run(body("range(0, 18, 2)"))).toHaveLength(1);
      expect(run(body("range(n)"))).toHaveLength(1);
    });
    it("a non-constant item in a short literal list does not count as constant", () => {
      expect(run(body("['a', x]"))).toHaveLength(1);
    });
  });

  describe("Read inside the loop", () => {
    it("any other read of the accumulator in the loop body suppresses the finding", () => {
      const reads = ["live.update(s, refresh=True)", "n = len(s)", "ok = s == 'x'", 'out = f"{s}!"', "print(s)"];
      for (const read of reads) {
        const src = `def f(rows, live):\n    s = ""\n    for r in rows:\n        s += r\n        ${read}\n`;
        expect(run(src), read).toEqual([]);
        expect(run(src.replace("s += r", "s = s + r")), read).toEqual([]);
        const attr = `class C:\n    def __init__(self):\n        self.b = ""\n    def f(self, rows, live):\n        for r in rows:\n            self.b += r\n            ${read.replace(/\bs\b/g, "self.b")}\n`;
        expect(run(attr), read).toEqual([]);
      }
    });
    it("a read before the append, or in a condition, also suppresses", () => {
      expect(run('def f(rows):\n    s = ""\n    for r in rows:\n        if len(s) > 80:\n            break\n        s += r\n')).toEqual([]);
    });
    it("append-only twin is still flagged", () => {
      expect(run('def f(rows, live):\n    s = ""\n    for r in rows:\n        s += r\n    live.update(s)\n')).toHaveLength(1);
    });
    it("without the read, an attribute target is medium", () => {
      const attr = run('class C:\n    def __init__(self):\n        self.b = ""\n    def f(self, rows):\n        for r in rows:\n            self.b += r\n');
      expect(attr[0].confidence).toBe("medium");
    });
  });

  describe("Suppressions", () => {
    it("`# noqa` / `# noqa: CODE-C10.4` on the loop header or the statement suppresses", () => {
      expect(run(LOCAL.replace("in rows:", "in rows:  # noqa: CODE-C10.4"))).toEqual([]);
      expect(run(LOCAL.replace("s += r", "s += r  # noqa: CODE-C10.4"))).toEqual([]);
      expect(run(LOCAL.replace("in rows:", "in rows:  # noqa"))).toEqual([]);
      expect(run(LOCAL.replace("s += r", "s += r  # noqa"))).toEqual([]);
    });
    it("a noqa for another rule does not suppress", () => {
      expect(run(LOCAL.replace("s += r", "s += r  # noqa: F401"))).toHaveLength(1);
    });
  });

  describe("Robustness", () => {
    it("returns zero findings on invalid Python syntax", () => {
      const content = readFileSync(join(FIXTURES_DIR, "syntax_error.py"), "utf-8");
      const parsed = parsePythonSource("syntax_error.py", content);
      expect(parsed.hasSyntaxError).toBe(true);
      expect(checkCodeC104(parsed)).toEqual([]);
    });
    it("returns zero findings on an empty file", () => {
      expect(run("")).toEqual([]);
    });
  });

  describe("Identity and fingerprints", () => {
    it("identity is `string-concat-in-loop:<qualname>:<name>:<ordinal>`", () => {
      expect(run(LOCAL)[0].identity).toBe("string-concat-in-loop:f:s:0");
    });
    it("are stable when code above moves", () => {
      const before = run(LOCAL);
      const after = run("# header\n\nimport os\n\n" + LOCAL);
      expect(after[0].location.startLine).not.toBe(before[0].location.startLine);
      expect(after[0].identity).toBe(before[0].identity);
      expect(after[0].fingerprint).toBe(before[0].fingerprint);
    });
    it("repeated accumulations of one name in a scope get distinct ordinals and fingerprints", () => {
      const src = LOCAL + "    for r in rows:\n        s += r\n";
      const [a, b] = run(src.replace("    return s\n", "") + "    return s\n");
      expect(a.identity).toBe("string-concat-in-loop:f:s:0");
      expect(b.identity).toBe("string-concat-in-loop:f:s:1");
      expect(a.fingerprint).not.toBe(b.fingerprint);
    });
    it("methods use the class-qualified name", () => {
      const f = run('class B:\n    def __init__(self):\n        self.b = ""\n    def go(self, rows):\n        for r in rows:\n            self.b += r\n');
      expect(f[0].identity).toBe("string-concat-in-loop:B.go:self.b:0");
    });
  });

  describe("Contract v1", () => {
    const read = (f: string) => readFileSync(join(FIXTURES_DIR, f), "utf-8");

    it("positives: completed with line-free identities; syntax error is never certified", () => {
      const result = evaluate(staticInput("CODE-C10.4", { "positives.py": read("positives.py") }));
      expect(result.status).toBe("completed");
      expect(result.findings).toHaveLength(runCheckOnFixture("positives.py").length);
      expect(result.findings.every((f) => /string-concat-in-loop:/.test(f.identity))).toBe(true);
      expect(result.findings.every((f) => !/\bline\b|:\d{2,}$/.test(f.identity))).toBe(true);

      const broken = evaluate(staticInput("CODE-C10.4", { "syntax_error.py": read("syntax_error.py") }));
      expect(broken.status).not.toBe("completed");
      expect(["partial", "unavailable"]).toContain(broken.status);
      expect(broken.findings).toEqual([]);

      const mixed = evaluate(
        staticInput("CODE-C10.4", { "positives.py": read("positives.py"), "syntax_error.py": read("syntax_error.py") })
      );
      expect(mixed.status).not.toBe("completed");
    });

    it.skipIf(!validatorAvailable)("positives, negatives and syntax-error pairs pass shared validate_pair", () => {
      for (const files of [
        { "positives.py": read("positives.py") },
        { "negatives.py": read("negatives.py") },
        { "positives.py": read("positives.py"), "syntax_error.py": read("syntax_error.py") },
      ]) {
        const input = staticInput("CODE-C10.4", files);
        const verdict = validatePair(input, evaluate(input));
        expect(verdict.ok, verdict.output).toBe(true);
      }
    });
  });
});
