import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { parsePythonSource } from "../src/core/parse.js";
import { checkCodeC114 } from "../src/checks/code-c11-4/index.js";
import { evaluate } from "../src/contract.js";
import { staticInput, validatePair, validatorAvailable } from "./helpers/contract.js";

const FIXTURES_DIR = join(__dirname, "fixtures", "code-c11-4");

function runCheckOnFixture(relPath: string) {
  const content = readFileSync(join(FIXTURES_DIR, relPath), "utf-8");
  const parsed = parsePythonSource(relPath, content);
  expect(parsed.hasSyntaxError).toBe(false);
  return checkCodeC114(parsed);
}

function run(content: string, path = "inline.py") {
  return checkCodeC114(parsePythonSource(path, content));
}

describe("CODE-C11.4 blocking call in async def detector", () => {
  describe("Positives fixture", () => {
    const findings = runCheckOnFixture("positives.py");
    const bySymbol = (s: string) => findings.filter((f) => f.evidence.symbol === s);

    it("reports exactly one finding per blocking call", () => {
      expect(findings).toHaveLength(15);
      for (const f of findings) {
        expect(f.check).toBe("CODE-C11.4");
        expect(f.kind).toBe("blocking-call-in-async");
        expect(f.severity).toBe("medium");
        expect(f.evidenceTier).toBe("static");
        expect(f.impact.quantified).toBe(false);
        expect(f.detector.version).toBe("0.1.0");
        expect(f.fingerprint).toMatch(/^[0-9a-f]{16}$/);
        expect(f.references.map((r) => r.id).slice(0, 2)).toEqual(["SRC-01", "taxonomy-c11.4"]);
        expect(f.references.map((r) => r.url)).toEqual(
          expect.arrayContaining([
            "https://docs.astral.sh/ruff/rules/blocking-http-call-in-async-function/",
            "https://docs.python.org/3/library/asyncio-task.html",
          ])
        );
        expect(f.limitations.join(" ")).toMatch(/no energy cost/);
        expect(f.agentPrompt).toMatch(/await/);
      }
    });

    it("time.sleep (plain, aliased module, from-import) is high confidence", () => {
      // sleeper, aliased_module, aliased_from, Service.handler x2
      const sleeps = bySymbol("time.sleep");
      expect(sleeps).toHaveLength(5);
      expect(sleeps.every((f) => f.confidence === "high")).toBe(true);
    });

    it("requests.* is high confidence; Session variable is medium", () => {
      const reqs = bySymbol("requests.get");
      expect(reqs).toHaveLength(2);
      expect(reqs.map((f) => f.confidence).sort()).toEqual(["high", "medium"]);
      expect(bySymbol("requests.post")[0].confidence).toBe("high");
      const viaSession = reqs.find((f) => f.confidence === "medium")!;
      expect(viaSession.identity).toBe("blocking-call-in-async:session_var:requests.get:0");
      expect(viaSession.limitations.join(" ")).toMatch(/requests\.Session/);
    });

    it("urlopen, httpx, subprocess, os.system, os.popen, input are medium", () => {
      for (const s of ["urllib.request.urlopen", "httpx.get", "subprocess.run", "os.system", "os.popen", "input"]) {
        const f = bySymbol(s);
        expect(f, s).toHaveLength(1);
        expect(f[0].confidence, s).toBe("medium");
      }
    });

    it("open() is low confidence with the small-file caveat", () => {
      const f = bySymbol("open");
      expect(f).toHaveLength(1);
      expect(f[0].confidence).toBe("low");
      expect(f[0].limitations.join(" ")).toMatch(/small local files/);
    });

    it("identities are line-free with per-key ordinals", () => {
      const ids = bySymbol("time.sleep").map((f) => f.identity);
      expect(ids).toContain("blocking-call-in-async:sleeper:time.sleep:0");
      expect(ids).toContain("blocking-call-in-async:Service.handler:time.sleep:0");
      expect(ids).toContain("blocking-call-in-async:Service.handler:time.sleep:1");
      expect(new Set(findings.map((f) => f.fingerprint)).size).toBe(findings.length);
    });
  });

  describe("Negatives fixture", () => {
    it("reports nothing", () => {
      expect(runCheckOnFixture("negatives.py")).toEqual([]);
    });
  });

  describe("Similar negatives and boundaries (inline)", () => {
    it("plain def is not flagged", () => {
      expect(run("import time\ndef f():\n    time.sleep(1)\n")).toEqual([]);
    });

    it("nested def inside async def is not flagged, direct call alongside it is", () => {
      const src = "import time\nasync def f():\n    def g():\n        time.sleep(1)\n    time.sleep(2)\n    return g\n";
      const r = run(src);
      expect(r).toHaveLength(1);
      expect(r[0].location.startLine).toBe(5);
    });

    it("nested async def is analysed on its own with its own qualname", () => {
      const r = run("import time\nasync def outer():\n    async def inner():\n        time.sleep(1)\n    return inner\n");
      expect(r).toHaveLength(1);
      expect(r[0].identity).toBe("blocking-call-in-async:outer.inner:time.sleep:0");
    });

    it("lambda passed to to_thread is not flagged", () => {
      expect(run("import asyncio, time\nasync def f():\n    await asyncio.to_thread(lambda: time.sleep(1))\n")).toEqual([]);
    });

    it("awaited call is skipped but a blocking argument of it is flagged", () => {
      const r = run("import time\nasync def f(c):\n    await c.run(time.sleep(1))\n");
      expect(r).toHaveLength(1);
      expect(r[0].evidence.symbol).toBe("time.sleep");
    });

    it("passing time.sleep without calling it is not flagged", () => {
      expect(run("import asyncio, time\nasync def f():\n    await asyncio.to_thread(time.sleep, 1)\n")).toEqual([]);
    });

    it("an unrelated object's .sleep/.get is not flagged", () => {
      expect(run("async def f(o):\n    o.sleep(1)\n    o.get('x')\n")).toEqual([]);
    });

    it("session-like name not bound to requests.Session is not flagged", () => {
      expect(run("import requests\nasync def f(s):\n    s.get('u')\n")).toEqual([]);
    });

    it("imported name shadowing builtin open is not treated as builtin", () => {
      expect(run("from aiofiles import open\nasync def f():\n    open('x')\n")).toEqual([]);
    });

    it("with-as Session is tracked", () => {
      const r = run("import requests\nasync def f():\n    with requests.Session() as s:\n        s.post('u')\n");
      expect(r).toHaveLength(1);
      expect(r[0].evidence.symbol).toBe("requests.post");
      expect(r[0].confidence).toBe("medium");
    });

    it("from-imports of subprocess/os/urllib resolve", () => {
      const src =
        "from subprocess import check_output\nfrom os import system\nfrom urllib import request\nasync def f():\n    check_output('x')\n    system('x')\n    request.urlopen('u')\n";
      expect(run(src).map((f) => f.evidence.symbol)).toEqual(["subprocess.check_output", "os.system", "urllib.request.urlopen"]);
    });
  });

  describe("Suppressions", () => {
    const src = "import time\nasync def f():\n    time.sleep(1)\n";
    it("# noqa, # noqa: CODE-C11.4 and # noqa: ASYNC2xx suppress; other codes do not", () => {
      expect(run(src.replace("(1)", "(1)  # noqa"))).toEqual([]);
      expect(run(src.replace("(1)", "(1)  # noqa: CODE-C11.4"))).toEqual([]);
      expect(run(src.replace("(1)", "(1)  # noqa: ASYNC251"))).toEqual([]);
      expect(run(src.replace("(1)", "(1)  # noqa: F401"))).toHaveLength(1);
    });
  });

  describe("Malformed input", () => {
    it("returns zero findings on invalid Python syntax", () => {
      const content = readFileSync(join(FIXTURES_DIR, "syntax_error.py"), "utf-8");
      const parsed = parsePythonSource("syntax_error.py", content);
      expect(parsed.hasSyntaxError).toBe(true);
      expect(checkCodeC114(parsed)).toEqual([]);
    });

    it("returns zero findings on an empty file", () => {
      expect(run("")).toEqual([]);
    });
  });

  describe("Identity and fingerprints", () => {
    const src = "import time\nasync def f():\n    time.sleep(1)\n";

    it("are stable across line shifts", () => {
      const before = run(src);
      const after = run("# header\n\nimport os\n" + src);
      expect(before).toHaveLength(1);
      expect(after[0].fingerprint).toBe(before[0].fingerprint);
      expect(after[0].identity).toBe(before[0].identity);
    });

    it("identical calls in one scope get distinct fingerprints", () => {
      const [a, b] = run(src + "    time.sleep(1)\n");
      expect(a.fingerprint).not.toBe(b.fingerprint);
    });
  });

  describe("Contract v1", () => {
    const read = (f: string) => readFileSync(join(FIXTURES_DIR, f), "utf-8");

    it("positives: completed with line-free identities; syntax error is never certified", () => {
      const result = evaluate(staticInput("CODE-C11.4", { "positives.py": read("positives.py") }));
      expect(result.status).toBe("completed");
      expect(result.findings).toHaveLength(runCheckOnFixture("positives.py").length);
      expect(result.findings.every((f) => f.identity.includes("blocking-call-in-async:"))).toBe(true);

      const broken = evaluate(staticInput("CODE-C11.4", { "syntax_error.py": read("syntax_error.py") }));
      expect(broken.status).toBe("unavailable");
      expect(broken.findings).toEqual([]);

      const partial = evaluate(
        staticInput("CODE-C11.4", { "positives.py": read("positives.py"), "syntax_error.py": read("syntax_error.py") })
      );
      expect(partial.status).not.toBe("completed");
    });

    it.skipIf(!validatorAvailable)("positives, negatives and syntax-error pairs pass shared validate_pair", () => {
      for (const files of [
        { "positives.py": read("positives.py") },
        { "negatives.py": read("negatives.py") },
        { "positives.py": read("positives.py"), "syntax_error.py": read("syntax_error.py") },
      ]) {
        const input = staticInput("CODE-C11.4", files);
        const verdict = validatePair(input, evaluate(input));
        expect(verdict.ok, verdict.output).toBe(true);
      }
    });
  });
});