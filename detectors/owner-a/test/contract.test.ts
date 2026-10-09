import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import {
  ContractInputError,
  contractFingerprint,
  evaluate,
} from "../src/contract.js";
import { staticInput, validatePair, validatorAvailable } from "./helpers/contract.js";

const C11_FIXTURES = join(__dirname, "fixtures", "code-c1-1");
const fixture = (name: string) => readFileSync(join(C11_FIXTURES, name), "utf-8");

describe("Owner-a detector contract v1 adapter", () => {
  describe("Fingerprint", () => {
    it("matches the shared OBS-01 reference vector", () => {
      expect(
        contractFingerprint(
          "github:AWS-env/example",
          "OBS-01",
          "file:config/production.yaml",
          "production-log-level"
        )
      ).toBe("22ff2e3a29b6270027d8f01268ac9e3683e44f707cbd979fb88af6b33e0c9aab");
    });
  });

  describe("CODE-C1.1 input/result pairs", () => {
    it("completed: positives produce findings with exact-line evidence", () => {
      const input = staticInput("CODE-C1.1", { "pkg/positives.py": fixture("positives.py") });
      const result = evaluate(input);

      expect(result.status).toBe("completed");
      expect(result.coverage.evaluated_scope).toEqual(["file:pkg/positives.py"]);
      expect(result.coverage.limitations[0]).toMatch(/^Static analysis only/);
      expect(result.measurements).toEqual([]);
      expect(result.findings.length).toBeGreaterThanOrEqual(6);

      const lines = fixture("positives.py").split("\n");
      for (const f of result.findings) {
        expect(f.fingerprint).toMatch(/^[0-9a-f]{64}$/);
        expect(f.identity.startsWith("pkg/positives.py::")).toBe(true);
        expect(f.identity).not.toMatch(/:\d+$/); // no line numbers in identity
        expect(f.references.every((r) => r.startsWith("https://"))).toBe(true);
        const ev = f.evidence[0];
        expect(ev.kind).toBe("static");
        expect(ev.value.split("\n")[0]).toBe(lines[ev.line_start - 1]);
      }
      expect(new Set(result.findings.map((f) => f.fingerprint)).size).toBe(result.findings.length);
    });

    it("completed with no findings on negatives (not a parse failure)", () => {
      const result = evaluate(staticInput("CODE-C1.1", { "negatives.py": fixture("negatives.py") }));
      expect(result.status).toBe("completed");
      expect(result.findings).toEqual([]);
    });

    it("keeps identities and fingerprints when code above moves", () => {
      const src = fixture("positives.py");
      const before = evaluate(staticInput("CODE-C1.1", { "m.py": src }));
      const after = evaluate(staticInput("CODE-C1.1", { "m.py": "# moved down\n\n" + src }));
      expect(after.findings.map((f) => f.fingerprint).sort()).toEqual(
        before.findings.map((f) => f.fingerprint).sort()
      );
    });

    it.skipIf(!validatorAvailable)("every pair passes shared validate_pair (python)", () => {
      const cases = [
        staticInput("CODE-C1.1", { "positives.py": fixture("positives.py") }),
        staticInput("CODE-C1.1", { "negatives.py": fixture("negatives.py") }),
        staticInput("CODE-C1.1", {
          "positives.py": fixture("positives.py"),
          "syntax_error.py": fixture("syntax_error.py"),
        }),
        staticInput("CODE-C1.1", { "syntax_error.py": fixture("syntax_error.py") }),
      ];
      for (const input of cases) {
        const result = evaluate(input);
        const verdict = validatePair(input, result);
        expect(verdict.ok, `${input.scope.join(",")}: ${verdict.output}`).toBe(true);
      }
    });

    it.skipIf(!validatorAvailable)("the validator rejects invented evidence and overstated coverage", () => {
      const input = staticInput("CODE-C1.1", { "positives.py": fixture("positives.py") });
      const tampered = evaluate(input);
      tampered.findings[0].evidence[0].value = "import not_in_the_file";
      expect(validatePair(input, tampered).ok).toBe(false);

      const broken = staticInput("CODE-C1.1", { "broken.py": fixture("syntax_error.py") });
      const overstated = { ...evaluate(broken), status: "completed" as const };
      overstated.coverage = { evaluated_scope: ["file:broken.py"], limitations: [] };
      // Shape-valid, but the adapter would never emit it: completed without evaluation.
      expect(evaluate(broken).status).not.toBe("completed");
      expect(validatePair(input, overstated).ok).toBe(false);
    });
  });

  describe("Coverage is never overstated", () => {
    it("partial: one scope fails to parse, findings only for the evaluated scope", () => {
      const result = evaluate(
        staticInput("CODE-C1.1", {
          "ok.py": fixture("positives.py"),
          "broken.py": fixture("syntax_error.py"),
        })
      );
      expect(result.status).toBe("partial");
      expect(result.coverage.evaluated_scope).toEqual(["file:ok.py"]);
      expect(result.coverage.limitations.some((l) => l.includes("broken.py could not be parsed"))).toBe(true);
      expect(result.findings.every((f) => f.scope_id === "file:ok.py")).toBe(true);
    });

    it("unavailable: the only scope fails to parse", () => {
      const result = evaluate(staticInput("CODE-C1.1", { "broken.py": fixture("syntax_error.py") }));
      expect(result.status).toBe("unavailable");
      expect(result.coverage.evaluated_scope).toEqual([]);
      expect(result.findings).toEqual([]);
    });

    it("unavailable: required static source missing for the scope", () => {
      const input = staticInput("CODE-C1.1", { "a.py": "import os\n" });
      input.sources = [];
      const result = evaluate(input);
      expect(result.status).toBe("unavailable");
      expect(result.coverage.limitations.some((l) => l.includes("no static source"))).toBe(true);
    });

    it("unavailable: non-Python source", () => {
      const result = evaluate(staticInput("CODE-C1.1", { "app.js": "import x from 'y'\n" }));
      expect(result.status).toBe("unavailable");
      expect(result.coverage.limitations.some((l) => l.includes("Python (.py/.pyi) only"))).toBe(true);
    });

    it("unavailable: ambiguous line separators that evidence cannot cite exactly", () => {
      const result = evaluate(staticInput("CODE-C1.1", { "ff.py": "import os\f\nx = 1\n" }));
      expect(result.status).toBe("unavailable");
    });

    it("unavailable: check not implemented by owner-a", () => {
      const result = evaluate(staticInput("OBS-01", { "a.py": "x = 1\n" }));
      expect(result.status).toBe("unavailable");
      expect(result.coverage.limitations[0]).toContain("not implemented by the owner-a detector");
    });

    it("unavailable: detector_version mismatch", () => {
      const result = evaluate(
        staticInput("CODE-C1.1", { "a.py": "x = 1\n" }, { detector_version: "9.9.9" })
      );
      expect(result.status).toBe("unavailable");
    });

    it("error: a failing check is reported, not certified", () => {
      const boom = new Map([
        ["CODE-C1.1", { version: "0.1.0", limitations: [], run: () => { throw new Error("boom"); } }],
      ]);
      const result = evaluate(staticInput("CODE-C1.1", { "a.py": "x = 1\n" }), boom);
      expect(result.status).toBe("error");
      expect(result.findings).toEqual([]);
      expect(result.coverage.evaluated_scope).toEqual([]);
    });

    it("rejects payloads that are not contract v1 inputs", () => {
      expect(() => evaluate({ schema_version: "0.9", kind: "input" })).toThrow(ContractInputError);
      expect(() => evaluate({ ...staticInput("CODE-C1.1", { "a.py": "" }), kind: "result" })).toThrow(
        ContractInputError
      );
    });

    it("echoes every identity field of the input", () => {
      const input = staticInput("CODE-C1.1", { "a.py": "import os\n" });
      const result = evaluate(input);
      for (const key of ["schema_version", "repository_id", "scan_id", "commit_sha", "check_id", "detector_version", "context", "scope"] as const) {
        expect(result[key]).toEqual(input[key]);
      }
    });
  });
});
