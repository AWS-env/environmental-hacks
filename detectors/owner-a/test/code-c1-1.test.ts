import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { parsePythonSource } from "../src/core/parse.js";
import { checkCodeC11 } from "../src/checks/code-c1-1/index.js";

const FIXTURES_DIR = join(__dirname, "fixtures", "code-c1-1");

function runCheckOnFixture(relPath: string) {
  const fullPath = join(FIXTURES_DIR, relPath);
  const content = readFileSync(fullPath, "utf-8");
  const parsed = parsePythonSource(relPath, content);
  return checkCodeC11(parsed);
}

describe("CODE-C1.1 Dead code / unused results detector", () => {
  describe("Positives fixture", () => {
    it("detects unused imports and unreachable code in positives.py", () => {
      const findings = runCheckOnFixture("positives.py");

      expect(findings.length).toBeGreaterThanOrEqual(6);

      // 1. Verify unused heavy import 'pd' (pandas)
      const pdFinding = findings.find(
        (f) => f.kind === "unused-import" && f.evidence.symbol === "pd"
      );
      expect(pdFinding).toBeDefined();
      expect(pdFinding?.evidence.module).toBe("pandas");
      expect(pdFinding?.evidence.costTier).toBe("heavy");
      expect(pdFinding?.severity).toBe("high");
      expect(pdFinding?.confidence).toBe("high");
      expect(pdFinding?.references[0].id).toBe("SRC-01");
      expect(pdFinding?.agentPrompt).toContain("pandas");

      // 2. Verify unused light import 'cos' (math)
      const cosFinding = findings.find(
        (f) => f.kind === "unused-import" && f.evidence.symbol === "cos"
      );
      expect(cosFinding).toBeDefined();
      expect(cosFinding?.evidence.module).toBe("math");
      expect(cosFinding?.evidence.costTier).toBe("light");
      expect(cosFinding?.severity).toBe("low");

      // 3. Verify function-local unused import 'scipy'
      const scipyFinding = findings.find(
        (f) => f.kind === "unused-import" && f.evidence.symbol === "scipy"
      );
      expect(scipyFinding).toBeDefined();
      expect(scipyFinding?.evidence.costTier).toBe("heavy");

      // 4. Verify unreachable code after return
      const afterReturn = findings.find(
        (f) =>
          f.kind === "unreachable-code" &&
          f.evidence.snippet.includes("dead_code = 123")
      );
      expect(afterReturn).toBeDefined();
      expect(afterReturn?.severity).toBe("info");

      // 5. Verify unreachable code after raise
      const afterRaise = findings.find(
        (f) =>
          f.kind === "unreachable-code" &&
          f.evidence.snippet.includes("unreachable after raise")
      );
      expect(afterRaise).toBeDefined();

      // 6. Verify unreachable code in if False:
      const constFalse = findings.find(
        (f) =>
          f.kind === "unreachable-code" &&
          f.evidence.snippet.includes("unreachable constant false branch")
      );
      expect(constFalse).toBeDefined();

      // 7. Verify unreachable code in while 0:
      const whileZero = findings.find(
        (f) =>
          f.kind === "unreachable-code" &&
          f.evidence.snippet.includes("unreachable while 0 loop")
      );
      expect(whileZero).toBeDefined();
    });
  });

  describe("Negatives fixture", () => {
    it("reports zero findings on negatives.py where imports are used or guarded", () => {
      const findings = runCheckOnFixture("negatives.py");
      expect(findings).toEqual([]);
    });
  });

  describe("References", () => {
    it("every finding cites the paper and the taxonomy issue #34", () => {
      const findings = runCheckOnFixture("positives.py");
      expect(findings.length).toBeGreaterThan(0);
      for (const f of findings) {
        expect(f.references.map((r) => r.id)).toEqual(["SRC-01", "taxonomy-c1.1"]);
        expect(f.references[1].url).toMatch(/\/issues\/34$/);
      }
    });
  });

  describe("Package and test module guards", () => {
    it("does not flag intentional re-exports in __init__.py", () => {
      const findings = runCheckOnFixture("pkg/__init__.py");
      expect(findings).toEqual([]);
    });

    it("does not flag pytest fixture imports in conftest.py", () => {
      const findings = runCheckOnFixture("conftest.py");
      expect(findings).toEqual([]);
    });
  });

  describe("Explicit re-exports (audit F2)", () => {
    const run = (code: string, path = "mod.py") => checkCodeC11(parsePythonSource(path, code));
    const unused = (code: string, path?: string) => run(code, path).filter((f) => f.kind === "unused-import");

    it("`from m import X as X` is an explicit re-export, not an unused import", () => {
      expect(unused("from pydantic import FieldInfo as FieldInfo\n")).toEqual([]);
    });

    it("`import X as X` is an explicit re-export too", () => {
      expect(unused("import os as os\n")).toEqual([]);
    });

    it("a parenthesised multi-name re-export block is skipped name by name", () => {
      const code = "from pkg._shared import (\n    Handler as Handler,\n    Other as Other,\n)\n";
      expect(unused(code)).toEqual([]);
    });

    it("a real alias that is never used is still flagged", () => {
      const f = unused("from pandas import DataFrame as DF\n");
      expect(f).toHaveLength(1);
      expect(f[0].evidence.symbol).toBe("DF");
    });

    it("an unused plain import next to a re-export is still flagged", () => {
      const f = unused("from m import A as A, B\n");
      expect(f.map((x) => x.evidence.symbol)).toEqual(["B"]);
    });

    it("an unused plain import in a compat module is reported at low confidence", () => {
      const [f] = unused("from urllib.parse import quote\n", "src/requests/compat.py");
      expect(f.confidence).toBe("low");
      expect(f.limitations.some((l) => l.includes("compat"))).toBe(true);
    });

    it("the same import outside a compat module keeps high confidence", () => {
      const [f] = unused("from urllib.parse import quote\n", "src/requests/utils.py");
      expect(f.confidence).toBe("high");
    });
  });

  describe("Generator marker `yield` after a terminal statement (audit F3)", () => {
    const unreachable = (code: string) =>
      checkCodeC11(parsePythonSource("gen.py", code)).filter((f) => f.kind === "unreachable-code");

    it("a bare `yield` after `raise` only makes the function a generator, so it is not reported", () => {
      expect(unreachable("async def g():\n    raise RuntimeError('boom')\n    yield\n")).toEqual([]);
    });

    it("`yield value` and `yield from` after `return` are not reported either", () => {
      expect(unreachable("def g():\n    return\n    yield 1\n")).toEqual([]);
      expect(unreachable("def g(xs):\n    return\n    yield from xs\n")).toEqual([]);
    });

    it("other statements after `raise` are still reported", () => {
      const f = unreachable("def g():\n    raise RuntimeError('boom')\n    print('never')\n");
      expect(f).toHaveLength(1);
      expect(f[0].evidence.snippet).toBe("print('never')");
    });

    it("a statement after the marker yield is still reported", () => {
      const f = unreachable("def g():\n    raise RuntimeError('boom')\n    yield\n    print('never')\n");
      expect(f.map((x) => x.evidence.snippet)).toEqual(["print('never')"]);
    });
  });

  describe("Dynamic usage confidence downgrade", () => {
    it("downgrades confidence to medium when module uses globals() or getattr()", () => {
      const code = `
import heavy_module
x = globals()["heavy_module"]
`;
      // When globals() is present, if an import is not explicitly referenced as an identifier,
      // confidence should be downgraded to medium.
      const unrefCode = `
import unused_mod
val = getattr(some_obj, "attr")
`;
      const parsed = parsePythonSource("dynamic_test.py", unrefCode);
      const findings = checkCodeC11(parsed);
      const unusedFinding = findings.find((f) => f.kind === "unused-import");
      expect(unusedFinding).toBeDefined();
      expect(unusedFinding?.confidence).toBe("medium");
      expect(unusedFinding?.limitations.some((l) => l.includes("dynamic"))).toBe(true);
    });
  });

  describe("Robustness against syntax errors", () => {
    it("gracefully returns zero findings on invalid Python syntax without crashing", () => {
      const findings = runCheckOnFixture("syntax_error.py");
      expect(findings).toEqual([]);
    });
  });

  describe("Deterministic fingerprints", () => {
    it("generates consistent 16-char hex fingerprints for identical signals", () => {
      const code = "import torch\n";
      const parsed1 = parsePythonSource("app.py", code);
      const parsed2 = parsePythonSource("app.py", code);

      const findings1 = checkCodeC11(parsed1);
      const findings2 = checkCodeC11(parsed2);

      expect(findings1[0].fingerprint).toHaveLength(16);
      expect(findings1[0].fingerprint).toBe(findings2[0].fingerprint);
    });
  });
});
