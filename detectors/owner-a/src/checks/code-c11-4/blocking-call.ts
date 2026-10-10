import Parser from "tree-sitter";
import { Confidence, Finding, generateFingerprint } from "../../core/finding.js";
import { isLineSuppressed } from "../../core/suppressions.js";
import { collectImportAliases, getEnclosingQualname, resolveCallee } from "../../core/loops.js";

const CHECK = "CODE-C11.4";
const KIND = "blocking-call-in-async";
const DETECTOR_VERSION = "0.1.0";

const REFERENCES = [
  {
    id: "SRC-01",
    title: "Watts This Smell: A Comprehensive Taxonomy of Software Energy Smells",
    url: "https://arxiv.org/abs/2604.04809",
  },
  {
    id: "taxonomy-c11.4",
    title: "Software Compute Waste Taxonomy - C11.4 Blocking the main thread",
    url: "https://github.com/AWS-env/environmental-hacks/issues/51",
  },
  {
    id: "ruff-async210",
    title: "Ruff ASYNC210 blocking-http-call-in-async-function",
    url: "https://docs.astral.sh/ruff/rules/blocking-http-call-in-async-function/",
  },
  {
    id: "ruff-async220",
    title: "Ruff ASYNC220 create-subprocess-in-async-function",
    url: "https://docs.astral.sh/ruff/rules/create-subprocess-in-async-function/",
  },
  {
    id: "ruff-async230",
    title: "Ruff ASYNC230 blocking-open-call-in-async-function",
    url: "https://docs.astral.sh/ruff/rules/blocking-open-call-in-async-function/",
  },
  {
    id: "ruff-async250",
    title: "Ruff ASYNC250 blocking-input-in-async-function",
    url: "https://docs.astral.sh/ruff/rules/blocking-input-in-async-function/",
  },
  {
    id: "ruff-async251",
    title: "Ruff ASYNC251 blocking-sleep-in-async-function",
    url: "https://docs.astral.sh/ruff/rules/blocking-sleep-in-async-function/",
  },
  {
    id: "python-asyncio-task",
    title: "Python docs: asyncio coroutines and tasks (asyncio.to_thread)",
    url: "https://docs.python.org/3/library/asyncio-task.html",
  },
];

const LIMITATIONS = [
  "Static only: the event-loop stall is a latency/throughput effect; no energy cost is measured or demonstrated by any source read for this check.",
  "SRC-01 barely covers concurrency (single-threaded dataset), so this finding rests on the linter documentation (Ruff ASYNC2xx), not on measured energy data.",
  "Moving the call to `asyncio.to_thread` is not automatically a saving: it adds thread hand-off overhead, and short uncontended synchronous I/O can be fine.",
];

const HTTP_METHODS = ["get", "post", "put", "patch", "delete", "head", "options", "request"];
const BLOCKING: ReadonlyMap<string, Confidence> = new Map<string, Confidence>([
  ["time.sleep", "high"],
  ...HTTP_METHODS.map((m): [string, Confidence] => [`requests.${m}`, "high"]),
  ["urllib.request.urlopen", "medium"],
  ...HTTP_METHODS.map((m): [string, Confidence] => [`httpx.${m}`, "medium"]),
  ["subprocess.run", "medium"],
  ["subprocess.call", "medium"],
  ["subprocess.check_call", "medium"],
  ["subprocess.check_output", "medium"],
  ["os.system", "medium"],
  ["os.popen", "medium"],
  ["input", "medium"],
  ["open", "low"],
]);

const SUGGESTION: Record<string, string> = {
  "time.sleep": "`await asyncio.sleep(...)`",
  input: "`await asyncio.to_thread(input, ...)`",
  open: "`await asyncio.to_thread(...)` around the file read/write (or leave it if the file is small and local)",
};

/** Codes accepted in `# noqa: ...` besides the blanket form: ours and the Ruff ASYNC2xx family. */
const NOQA_CODES = [CHECK, ...Array.from({ length: 100 }, (_, i) => `ASYNC${200 + i}`)];

const isAsyncDef = (n: Parser.SyntaxNode) =>
  n.type === "function_definition" && n.children.some((c) => c.type === "async");

const NESTED_SCOPES = new Set(["function_definition", "class_definition", "lambda"]);

/** Names bound to `requests.Session()` directly in this async def (assignment or `with ... as`). */
function collectSessionNames(
  fn: Parser.SyntaxNode,
  aliases: ReadonlyMap<string, string>
): Set<string> {
  const names = new Set<string>();
  const isSession = (v: Parser.SyntaxNode | null) =>
    v?.type === "call" &&
    resolveCallee(v.childForFieldName("function")?.text ?? "", aliases) === "requests.Session";
  const visit = (n: Parser.SyntaxNode) => {
    if (n.type === "assignment") {
      const left = n.childForFieldName("left");
      if (left?.type === "identifier" && isSession(n.childForFieldName("right"))) names.add(left.text);
    } else if (n.type === "as_pattern") {
      const target = n.namedChildren.find((c) => c.type === "as_pattern_target");
      const id = target?.namedChildren[0];
      if (id?.type === "identifier" && isSession(n.namedChildren[0])) names.add(id.text);
    }
    for (const c of n.namedChildren) if (!NESTED_SCOPES.has(c.type)) visit(c);
  };
  visit(fn.childForFieldName("body") ?? fn);
  return names;
}

interface Site {
  call: Parser.SyntaxNode;
  callee: string;
  confidence: Confidence;
  viaSession: boolean;
}

function collectSites(fn: Parser.SyntaxNode, aliases: ReadonlyMap<string, string>): Site[] {
  const sessions = collectSessionNames(fn, aliases);
  const sites: Site[] = [];
  const visit = (n: Parser.SyntaxNode, awaitedCall: Parser.SyntaxNode | null) => {
    if (n.type === "call" && n.id !== awaitedCall?.id) {
      const f = n.childForFieldName("function");
      if (f && (f.type === "identifier" || f.type === "attribute")) {
        let callee = resolveCallee(f.text, aliases);
        let viaSession = false;
        if (f.type === "attribute") {
          const obj = f.childForFieldName("object");
          const method = f.childForFieldName("attribute")?.text ?? "";
          if (obj?.type === "identifier" && sessions.has(obj.text) && HTTP_METHODS.includes(method)) {
            callee = `requests.${method}`;
            viaSession = true;
          }
        }
        const confidence = BLOCKING.get(callee);
        if (confidence) {
          sites.push({ call: n, callee, confidence: viaSession ? "medium" : confidence, viaSession });
        }
      }
    }
    // A directly awaited call is not blocking; its arguments still run synchronously.
    const nextAwaited = n.type === "await" ? n.namedChildren[0] ?? null : null;
    for (const c of n.namedChildren) {
      if (!NESTED_SCOPES.has(c.type)) visit(c, nextAwaited);
    }
  };
  visit(fn.childForFieldName("body") ?? fn, null);
  return sites;
}

function messageFor(callee: string): string {
  if (callee === "time.sleep") return "`time.sleep` blocks the whole event loop for its duration; every other task on the loop is stalled.";
  if (callee.startsWith("requests.") || callee.startsWith("httpx.") || callee === "urllib.request.urlopen") {
    return `\`${callee}\` is a synchronous HTTP call; the event loop cannot run other tasks while it waits for the network.`;
  }
  if (callee.startsWith("subprocess.") || callee === "os.system" || callee === "os.popen") {
    return `\`${callee}\` waits for a child process synchronously, stalling the event loop meanwhile.`;
  }
  if (callee === "input") return "`input()` blocks the event loop until the user responds.";
  return "`open()` performs synchronous file I/O on the event loop thread.";
}

function promptFor(callee: string): string {
  if (SUGGESTION[callee]) return `replace it with ${SUGGESTION[callee]}`;
  if (callee.startsWith("requests.") || callee.startsWith("httpx.") || callee === "urllib.request.urlopen") {
    return "use an async client (`httpx.AsyncClient` or `aiohttp`) with `await`, or wrap the call in `await asyncio.to_thread(...)`";
  }
  return "use `asyncio.create_subprocess_exec`/`create_subprocess_shell` with `await`, or wrap the call in `await asyncio.to_thread(...)`";
}

/**
 * Pure function detecting blocking calls made directly inside `async def` bodies
 * (CODE-C11.4). Never performs I/O or executes code.
 */
export function detectBlockingCallInAsync(
  rootNode: Parser.SyntaxNode,
  filePath: string,
  sourceLines: string[]
): Finding[] {
  const aliases = collectImportAliases(rootNode);
  const findings: Finding[] = [];
  const ordinals = new Map<string, number>();

  const walk = (n: Parser.SyntaxNode) => {
    if (isAsyncDef(n)) {
      for (const s of collectSites(n, aliases)) {
        const row = s.call.startPosition.row;
        if (isLineSuppressed(sourceLines[row] ?? "", NOQA_CODES).isSuppressed) continue;

        const qualname = getEnclosingQualname(s.call);
        const key = `${qualname}:${s.callee}`;
        const ordinal = ordinals.get(key) ?? 0;
        ordinals.set(key, ordinal + 1);

        const limitations = [...LIMITATIONS];
        if (s.callee === "open") {
          limitations.push("Ruff ASYNC230 flags open() in async code, but small local files can be cheaper to read synchronously than to hand off to a thread.");
        }
        if (s.viaSession) {
          limitations.push("Callee inferred from a variable bound to `requests.Session()` in the same function.");
        }

        findings.push({
          check: CHECK,
          kind: KIND,
          fingerprint: generateFingerprint(CHECK, KIND, filePath, `${key}:${ordinal}`),
          identity: `${KIND}:${key}:${ordinal}`,
          location: { path: filePath, startLine: row + 1, endLine: s.call.endPosition.row + 1 },
          evidence: { snippet: (sourceLines[row] ?? "").trim(), symbol: s.callee },
          why: `${messageFor(s.callee)} Called directly in async function '${qualname}' without await.`,
          severity: "medium",
          confidence: s.confidence,
          limitations,
          evidenceTier: "static",
          impact: {
            quantified: false,
            reason: "Event-loop stall is a latency/throughput effect; blocked duration is not measured statically and no energy effect is demonstrated.",
          },
          references: REFERENCES.map((r) => ({ ...r })),
          agentPrompt: `In ${filePath}:${row + 1}, ${promptFor(s.callee)}. Static finding only - keep the change minimal and covered by tests.`,
          detector: { id: "owner-a-static-scan", version: DETECTOR_VERSION },
        });
      }
    }
    for (const c of n.namedChildren) walk(c);
  };
  walk(rootNode);

  return findings.sort((a, b) => a.location.startLine - b.location.startLine);
}
