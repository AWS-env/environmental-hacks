import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { parsePythonSource } from "../src/core/parse.js";
import { checkCodeC66 } from "../src/checks/code-c6-6/index.js";
import { evaluate } from "../src/contract.js";
import { staticInput, validatePair, validatorAvailable } from "./helpers/contract.js";

const FIXTURES_DIR = join(__dirname, "fixtures", "code-c6-6");

function runCheckOnFixture(relPath: string) {
  const content = readFileSync(join(FIXTURES_DIR, relPath), "utf-8");
  const parsed = parsePythonSource(relPath, content);
  expect(parsed.hasSyntaxError).toBe(false);
  return checkCodeC66(parsed);
}

function run(content: string, path = "inline.py") {
  return checkCodeC66(parsePythonSource(path, content));
}

describe("CODE-C6.6 Leaked resource handles detector", () => {
  describe("Positives fixture", () => {
    const findings = runCheckOnFixture("positives.py");
    const byQual = (q: string) =>
      findings.find((f) => f.identity?.startsWith(`unclosed-handle:${q}:`));

    it("reports exactly one finding per leaking function with the shared shape", () => {
      expect(findings).toHaveLength(8);
      for (const f of findings) {
        expect(f.check).toBe("CODE-C6.6");
        expect(f.kind).toBe("unclosed-handle");
        expect(f.severity).toBe("low");
        expect(f.evidenceTier).toBe("static");
        expect(f.impact.quantified).toBe(false);
        expect(f.fingerprint).toMatch(/^[0-9a-f]{16}$/);
        expect(f.detector.version).toBe("0.1.0");
        expect(f.references.map((r) => r.id).slice(0, 2)).toEqual(["SRC-01", "taxonomy-c6.6"]);
        expect(f.references.map((r) => r.url)).toEqual(
          expect.arrayContaining([
            "https://docs.astral.sh/ruff/rules/open-file-with-context-handler/",
            "https://pylint.readthedocs.io/en/stable/user_guide/messages/refactor/consider-using-with.html",
            "https://codeql.github.com/codeql-query-help/python/py-file-not-closed/",
          ])
        );
        expect(f.agentPrompt).toContain("with ");
      }
    });

    it("open / socket / sqlite3 are medium confidence and name the callee", () => {
      const cases: Array<[string, string, string]> = [
        ["plain_open", "f", "open"],
        ["raw_socket", "s", "socket.socket"],
        ["db_connection", "conn", "sqlite3.connect"],
      ];
      for (const [qual, name, callee] of cases) {
        const f = byQual(qual)!;
        expect(f, qual).toBeDefined();
        expect(f.confidence).toBe("medium");
        expect(f.evidence.symbol).toBe(name);
        expect(f.evidence.factory).toBe(callee);
        expect(f.identity).toBe(`unclosed-handle:${qual}:${name}:${callee}:0`);
        expect(f.agentPrompt).toContain(`as ${name}:`);
      }
    });

    it("resolves import aliases (from io import open as o; import gzip as gz)", () => {
      expect(byQual("aliased_import")!.evidence.factory).toBe("io.open");
      expect(byQual("aliased_import")!.confidence).toBe("medium");
      expect(byQual("aliased_module")!.evidence.factory).toBe("gzip.open");
    });

    it("chained open().read()/write() is reported at low confidence", () => {
      for (const qual of ["chained_read", "chained_write"]) {
        const f = byQual(qual)!;
        expect(f, qual).toBeDefined();
        expect(f.confidence).toBe("low");
        expect(f.evidence.symbol).toBe("(chained)");
        expect(f.why).toContain("chained");
      }
    });

    it("a close outside finally is reported low and says it is not exception-safe", () => {
      const f = byQual("close_not_exception_safe")!;
      expect(f.confidence).toBe("low");
      expect(f.severity).toBe("low");
      expect(f.why).toContain("not exception-safe");
    });

    it("limitations are honest about evidence", () => {
      const text = findings[0].limitations.join(" ");
      expect(text).toContain("SRC-01 did not observe");
      expect(text).toContain("reference counting");
      expect(text).toContain("file descriptors");
    });
  });

  describe("Negatives fixture", () => {
    it("reports zero findings where every guard applies", () => {
      expect(runCheckOnFixture("negatives.py")).toEqual([]);
    });
  });

  describe("Similar negatives and guard twins", () => {
    const leak = "def f(p):\n    f = open(p)\n    return f.read()\n";

    it("the unguarded twin fires; each guard silences it", () => {
      expect(run(leak)).toHaveLength(1);
      expect(run("def f(p):\n    with open(p) as f:\n        return f.read()\n")).toEqual([]);
      expect(run("def f(p):\n    f = open(p)\n    return f\n")).toEqual([]);
      expect(run("def f(p, xs):\n    f = open(p)\n    xs.append(f)\n")).toEqual([]);
      expect(run("def f(p, s):\n    s.f = open(p)\n")).toEqual([]);
      expect(run("def f(p):\n    f = open(p)\n    yield f\n")).toEqual([]);
    });

    it("module-level handles are out of scope, even inside a module-level loop", () => {
      expect(run("f = open('x')\ndata = f.read()\n")).toEqual([]);
      expect(run("for p in PATHS:\n    f = open(p)\n    f.read()\n")).toEqual([]);
    });

    it("open from an unrelated module (os.open) or an attribute is not a handle callee", () => {
      expect(run("from os import open\n\ndef f(p):\n    fd = open(p, 0)\n    return fd + 1\n")).toEqual([]);
      expect(run("def f(path):\n    h = path.open()\n    return h.read()\n")).toEqual([]);
    });

    it("subprocess.Popen: terminate/kill/wait/communicate count as release only in finally", () => {
      const popen = (tail: string) =>
        `import subprocess\n\ndef f(c):\n    p = subprocess.Popen(c)\n${tail}`;
      expect(run(popen("    return 1\n"))).toHaveLength(1);
      const unsafe = run(popen("    p.wait()\n"));
      expect(unsafe).toHaveLength(1);
      expect(unsafe[0].confidence).toBe("low");
      expect(run(popen("    try:\n        pass\n    finally:\n        p.terminate()\n"))).toEqual([]);
      expect(run(popen("    out = p.communicate()\n"))[0].confidence).toBe("low");
    });

    it("other handle callees are recognized", () => {
      const callees: Array<[string, string]> = [
        ["import codecs\n", "codecs.open(p)"],
        ["import bz2\n", "bz2.open(p)"],
        ["import lzma\n", "lzma.open(p)"],
        ["import tarfile\n", "tarfile.open(p)"],
        ["import zipfile\n", "zipfile.ZipFile(p)"],
        ["import tempfile\n", "tempfile.NamedTemporaryFile()"],
        ["import tempfile\n", "tempfile.TemporaryFile()"],
        ["import tempfile\n", "tempfile.SpooledTemporaryFile()"],
        ["import socket\n", "socket.create_connection(p)"],
        ["import urllib.request\n", "urllib.request.urlopen(p)"],
        ["from urllib.request import urlopen\n", "urlopen(p)"],
        ["from zipfile import ZipFile as Z\n", "Z(p)"],
        ["import io\n", "io.open(p)"],
      ];
      for (const [imp, call] of callees) {
        const out = run(`${imp}\ndef f(p):\n    h = ${call}\n    return h.read()\n`);
        expect(out, call).toHaveLength(1);
      }
    });

    it("noqa on the statement line suppresses; an unrelated code does not", () => {
      expect(run(leak.replace("open(p)", "open(p)  # noqa"))).toEqual([]);
      expect(run(leak.replace("open(p)", "open(p)  # noqa: CODE-C6.6"))).toEqual([]);
      expect(run(leak.replace("open(p)", "open(p)  # noqa: SIM115"))).toEqual([]);
      expect(run(leak.replace("open(p)", "open(p)  # noqa: F401"))).toHaveLength(1);
    });
  });

  describe("Boundary: close in finally vs after", () => {
    const body = (tail: string) => `def f(p):\n    f = open(p)\n${tail}`;

    it("close in finally is safe", () => {
      expect(run(body("    try:\n        x = f.read()\n    finally:\n        f.close()\n"))).toEqual([]);
    });

    it("close after the use (straight line) is reported at low confidence", () => {
      const out = run(body("    x = f.read()\n    f.close()\n"));
      expect(out).toHaveLength(1);
      expect(out[0].confidence).toBe("low");
    });

    it("close in a try body or except (not finally) is still not exception-safe", () => {
      const out = run(body("    try:\n        x = f.read()\n        f.close()\n    except OSError:\n        pass\n"));
      expect(out).toHaveLength(1);
      expect(out[0].confidence).toBe("low");
    });

    it("never closed is medium; closed unsafely is low", () => {
      expect(run(body("    return f.read()\n"))[0].confidence).toBe("medium");
      expect(run(body("    f.close()\n"))[0].confidence).toBe("low");
    });
  });

  describe("Robustness", () => {
    it("returns zero findings on invalid Python syntax", () => {
      const content = readFileSync(join(FIXTURES_DIR, "syntax_error.py"), "utf-8");
      const parsed = parsePythonSource("syntax_error.py", content);
      expect(parsed.hasSyntaxError).toBe(true);
      expect(checkCodeC66(parsed)).toEqual([]);
    });

    it("returns zero findings on an empty file", () => {
      expect(run("")).toEqual([]);
    });
  });

  describe("Identity and fingerprints", () => {
    const src = "def f(p):\n    f = open(p)\n    return f.read()\n";

    it("are stable across line shifts above the finding", () => {
      const before = run(src);
      const after = run("# header\n\nimport os\n\n" + src);
      expect(before).toHaveLength(1);
      expect(after[0].fingerprint).toBe(before[0].fingerprint);
      expect(after[0].identity).toBe(before[0].identity);
      expect(after[0].location.startLine).not.toBe(before[0].location.startLine);
    });

    it("identical handles in one scope get distinct ordinals and fingerprints", () => {
      const [a, b] = run(
        "def f(p):\n    f = open(p)\n    x = f.read()\n    f = open(p)\n    return x + f.read()\n"
      );
      expect(a.identity).toBe("unclosed-handle:f:f:open:0");
      expect(b.identity).toBe("unclosed-handle:f:f:open:1");
      expect(a.fingerprint).not.toBe(b.fingerprint);
    });

    it("methods use the class-qualified name", () => {
      const out = run("class C:\n    def m(self, p):\n        f = open(p)\n        return f.read()\n");
      expect(out[0].identity).toBe("unclosed-handle:C.m:f:open:0");
    });
  });

  describe("Contract v1", () => {
    const read = (f: string) => readFileSync(join(FIXTURES_DIR, f), "utf-8");

    it("positives: completed with line-free identities; syntax error is never certified", () => {
      const result = evaluate(staticInput("CODE-C6.6", { "positives.py": read("positives.py") }));
      expect(result.status).toBe("completed");
      expect(result.findings).toHaveLength(runCheckOnFixture("positives.py").length);
      expect(result.findings.every((f) => /^positives\.py::unclosed-handle:[^:]+:[^:]+:[^:]+:\d+$/.test(f.identity))).toBe(true);

      const broken = evaluate(staticInput("CODE-C6.6", { "syntax_error.py": read("syntax_error.py") }));
      expect(broken.status).toBe("unavailable");
      expect(broken.findings).toEqual([]);

      const partial = evaluate(
        staticInput("CODE-C6.6", {
          "positives.py": read("positives.py"),
          "syntax_error.py": read("syntax_error.py"),
        })
      );
      expect(partial.status).toBe("partial");
    });

    it.skipIf(!validatorAvailable)("positives, negatives and syntax-error pairs pass shared validate_pair", () => {
      for (const files of [
        { "positives.py": read("positives.py") },
        { "negatives.py": read("negatives.py") },
        { "positives.py": read("positives.py"), "syntax_error.py": read("syntax_error.py") },
      ]) {
        const input = staticInput("CODE-C6.6", files);
        const verdict = validatePair(input, evaluate(input));
        expect(verdict.ok, verdict.output).toBe(true);
      }
    });
  });
});
