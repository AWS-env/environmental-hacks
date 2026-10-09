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
