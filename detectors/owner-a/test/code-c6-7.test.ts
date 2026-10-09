import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { parsePythonSource } from "../src/core/parse.js";
import { checkCodeC67 } from "../src/checks/code-c6-7/index.js";
import { evaluate } from "../src/contract.js";
import { staticInput, validatePair, validatorAvailable } from "./helpers/contract.js";

const FIXTURES_DIR = join(__dirname, "fixtures", "code-c6-7");
const read = (f: string) => readFileSync(join(FIXTURES_DIR, f), "utf-8");

function runCheckOnFixture(relPath: string) {
  const parsed = parsePythonSource(relPath, read(relPath));
  expect(parsed.hasSyntaxError).toBe(false);
  return checkCodeC67(parsed);
}

function run(content: string, path = "inline.py") {
  return checkCodeC67(parsePythonSource(path, content));
}

describe("CODE-C6.7 Leaking mutable defaults detector", () => {
  describe("Positives fixture", () => {
    const findings = runCheckOnFixture("positives.py");
    const byFn = (fn: string) => findings.find((f) => f.identity === `mutable-default-mutated:${fn}:${fn === "lambda_default.<lambda>" ? "acc" : (findings.find((g) => g.identity.startsWith(`mutable-default-mutated:${fn}:`))?.evidence.symbol ?? "")}`);

    it("reports exactly one finding per mutated mutable default", () => {
      expect(findings).toHaveLength(20);
      for (const f of findings) {
        expect(f.check).toBe("CODE-C6.7");
        expect(f.kind).toBe("mutable-default-mutated");
        expect(f.severity).toBe("medium");
        expect(f.evidenceTier).toBe("static");
        expect(f.impact.quantified).toBe(false);
        expect(f.fingerprint).toMatch(/^[0-9a-f]{16}$/);
        expect(f.detector.version).toBe("0.1.0");
        expect(f.references.map((r) => r.id).slice(0, 2)).toEqual(["SRC-01", "taxonomy-c6.7"]);
        expect(f.limitations.join(" ")).toMatch(/not on SRC-01/);
        expect(f.agentPrompt).toMatch(/`None`/);
        expect(f.agentPrompt).toMatch(/is None/);
      }
    });

    it("literal defaults mutated by a method call: high confidence", () => {
      for (const fn of ["list_literal", "dict_literal", "set_brace", "async_fn", "branch_only", "typed", "Service.method", "closure_mutation"]) {
        expect(byFn(fn), fn).toBeDefined();
        expect(byFn(fn)!.confidence, fn).toBe("high");
      }
      expect(byFn("list_literal")!.evidence.mutator).toBe("append");
      expect(byFn("dict_literal")!.evidence.mutator).toBe("setdefault");
    });

    it("comprehensions, constructor calls and non-method mutations: medium confidence", () => {
      for (const fn of [
        "set_literal", "comprehension", "dict_comp", "list_call", "defaultdict_call", "deque_call", "bytearray_call",
        "plus_equals", "item_assign", "delete_item",
      ]) {
        expect(byFn(fn), fn).toBeDefined();
        expect(byFn(fn)!.confidence, fn).toBe("medium");
      }
      expect(byFn("plus_equals")!.evidence.mutator).toBe("+=");
      expect(byFn("item_assign")!.evidence.mutator).toBe("item-assign");
      expect(byFn("delete_item")!.evidence.mutator).toBe("del");
    });

    it("lambda default is found", () => {
      const f = byFn("lambda_default.<lambda>");
      expect(f).toBeDefined();
      expect(f!.evidence.symbol).toBe("acc");
    });

    it("memo-style names are reported with low confidence and a memo limitation", () => {
      const f = byFn("memo_name")!;
      expect(f.confidence).toBe("low");
      expect(f.limitations.join(" ")).toMatch(/intentional|deliberate/i);
    });

    it("evidence is the def line plus the mutation line, identity is line-free", () => {
      const f = byFn("list_literal")!;
      expect(f.evidence.snippet).toBe("def list_literal(x, acc=[]):\nacc.append(x)");
      expect(f.identity).toBe("mutable-default-mutated:list_literal:acc");
      expect(f.location.startLine).toBe(6);
      expect(byFn("Service.method")!.identity).toBe("mutable-default-mutated:Service.method:acc");
      expect(f.references.map((r) => r.id)).toEqual(
        expect.arrayContaining(["ruff-B006", "pylint-W0102", "codeql-py-modification-of-default-value"])
      );
    });
  });

  describe("Negatives fixture (similar code and legitimate exceptions)", () => {
    it("reports nothing", () => {
      expect(runCheckOnFixture("negatives.py")).toEqual([]);
    });

    it("never-mutated default is not reported", () => {
      expect(run("def f(x, acc=[]):\n    return acc + [x]\n")).toEqual([]);
    });

    it("each re-binding form before the mutation suppresses", () => {
      for (const rebind of ["acc = list(acc)", "acc = acc or []", "acc = copy.copy(acc)", "if acc is None:\n        acc = []"]) {
        expect(run(`def f(x, acc=[]):\n    ${rebind}\n    acc.append(x)\n`), rebind).toEqual([]);
      }
    });

    it("noqa forms on def line, mutation line or parameter line suppress; unrelated noqa does not", () => {
      const src = "def f(x, acc=[]):\n    acc.append(x)\n";
      expect(run(src.replace("[]):", "[]):  # noqa: CODE-C6.7"))).toEqual([]);
      expect(run(src.replace("[]):", "[]):  # noqa: B006"))).toEqual([]);
      expect(run(src.replace("[]):", "[]):  # noqa"))).toEqual([]);
      expect(run(src.replace("(x)", "(x)  # noqa: CODE-C6.7"))).toEqual([]);
      expect(run(src.replace("[]):", "[]):  # noqa: E501"))).toHaveLength(1);
    });
  });

  describe("Boundaries", () => {
    it("a mutation only in a branch still counts", () => {
      expect(run("def f(x, flag, acc=[]):\n    if flag:\n        acc.append(x)\n")).toHaveLength(1);
    });

    it("a mutation after a re-binding does not count, before it does", () => {
      expect(run("def f(x, acc=[]):\n    acc = list(acc)\n    acc.append(x)\n")).toEqual([]);
      expect(run("def f(x, acc=[]):\n    acc.append(x)\n    acc = list(acc)\n    acc.append(x)\n")).toHaveLength(1);
    });

    it("a nested function that does not shadow the name mutates the default", () => {
      expect(run("def f(x, acc=[]):\n    def g():\n        acc.append(x)\n    g()\n")).toHaveLength(1);
    });

    it("only the first mutation is reported per parameter", () => {
      const out = run("def f(x, acc=[]):\n    acc.append(x)\n    acc.append(x)\n    acc[0] = 1\n");
      expect(out).toHaveLength(1);
      expect(out[0].location.startLine).toBe(2);
    });

    it("two mutable params in one function give two findings with distinct identities", () => {
      const out = run("def f(x, a=[], b={}):\n    a.append(x)\n    b[x] = 1\n");
      expect(out.map((f) => f.identity)).toEqual([
        "mutable-default-mutated:f:a",
        "mutable-default-mutated:f:b",
      ]);
    });

    it("annotation judgement: list annotation is still flagged, read-only annotations are not", () => {
      expect(run("def f(x, acc: list[int] = []):\n    acc.append(x)\n")).toHaveLength(1);
      expect(run("def f(x, acc: typing.Sequence[int] = []):\n    acc.append(x)\n")).toEqual([]);
      expect(run("def f(x, acc: frozenset = set()):\n    acc.add(x)\n")).toEqual([]);
    });

    it("immutable defaults are never flagged even if the name is mutated", () => {
      expect(run("def f(x, acc=(), n=None, s=frozenset()):\n    acc.append(x)\n    n.append(x)\n    s.add(x)\n")).toEqual([]);
    });
  });

  describe("Missing information", () => {
    it("functions without defaults or parameters give nothing", () => {
      expect(run("def f():\n    pass\n\ndef g(x, *args, **kw):\n    args.append(x)\n")).toEqual([]);
    });

    it("module without functions and an empty file give nothing", () => {
      expect(run("x = []\nx.append(1)\n")).toEqual([]);
      expect(run("")).toEqual([]);
    });

    it("a mutable default with a stub body gives nothing", () => {
      expect(run("def f(acc=[]):\n    ...\n")).toEqual([]);
    });
  });

  describe("Malformed input", () => {
    it("returns zero findings on invalid Python syntax", () => {
      const parsed = parsePythonSource("syntax_error.py", read("syntax_error.py"));
      expect(parsed.hasSyntaxError).toBe(true);
      expect(checkCodeC67(parsed)).toEqual([]);
    });
  });

  describe("Identity and fingerprints", () => {
    const src = "def f(x, acc=[]):\n    acc.append(x)\n";

    it("are stable when code above moves", () => {
      const before = run(src);
      const after = run("# header\nimport os\n\n\n" + src);
      expect(before).toHaveLength(1);
      expect(after[0].identity).toBe(before[0].identity);
      expect(after[0].fingerprint).toBe(before[0].fingerprint);
      expect(after[0].location.startLine).not.toBe(before[0].location.startLine);
    });

    it("repeated same-qualname function and param get distinct identities and fingerprints", () => {
      const [a, b] = run(src + "\n" + src);
      expect(a.identity).toBe("mutable-default-mutated:f:acc");
      expect(b.identity).toBe("mutable-default-mutated:f:acc:1");
      expect(a.fingerprint).not.toBe(b.fingerprint);
    });
  });

  describe("Contract v1", () => {
    it("positives: completed with line-free identities; syntax error is never certified", () => {
      const result = evaluate(staticInput("CODE-C6.7", { "positives.py": read("positives.py") }));
      expect(result.status).toBe("completed");
      expect(result.findings).toHaveLength(runCheckOnFixture("positives.py").length);
      expect(result.findings.every((f) => /mutable-default-mutated:/.test(f.identity))).toBe(true);

      const broken = evaluate(staticInput("CODE-C6.7", { "syntax_error.py": read("syntax_error.py") }));
      expect(broken.status).toBe("unavailable");
      expect(broken.findings).toEqual([]);

      const partial = evaluate(
        staticInput("CODE-C6.7", { "positives.py": read("positives.py"), "syntax_error.py": read("syntax_error.py") })
      );
      expect(partial.status).not.toBe("completed");
    });

    it.skipIf(!validatorAvailable)("positives, negatives and syntax-error pairs pass shared validate_pair", () => {
      for (const files of [
        { "positives.py": read("positives.py") },
        { "negatives.py": read("negatives.py") },
        { "positives.py": read("positives.py"), "syntax_error.py": read("syntax_error.py") },
      ]) {
        const input = staticInput("CODE-C6.7", files);
        const verdict = validatePair(input, evaluate(input));
        expect(verdict.ok, verdict.output).toBe(true);
      }
    });
  });
});
