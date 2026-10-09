import Parser from "tree-sitter";
import { Confidence, Finding, generateFingerprint } from "../../core/finding.js";
import { isLineSuppressed } from "../../core/suppressions.js";
import {
  collectImportAliases,
  getEnclosingQualname,
  resolveCallee,
} from "../../core/loops.js";

const CHECK = "CODE-C6.6";
const KIND = "unclosed-handle";
const DETECTOR_VERSION = "0.1.0";
const SUPPRESSION_CODES = [CHECK, "SIM115"];

const REFERENCES = [
  {
    id: "SRC-01",
    title:
      "Watts This Smell: A Comprehensive Taxonomy of Software Energy Smells",
    url: "https://arxiv.org/abs/2604.04809",
  },
  {
    id: "taxonomy-c6.6",
    title: "Software Compute Waste Taxonomy - C6.6 Leaked resource handles",
    url: "https://github.com/AWS-env/environmental-hacks/issues/84",
  },
  {
    id: "ruff-SIM115",
    title: "Ruff SIM115 open-file-with-context-handler",
    url: "https://docs.astral.sh/ruff/rules/open-file-with-context-handler/",
  },
  {
    id: "pylint-R1732",
    title: "Pylint R1732 consider-using-with",
    url: "https://pylint.readthedocs.io/en/stable/user_guide/messages/refactor/consider-using-with.html",
  },
  {
    id: "codeql-py-file-not-closed",
    title: "CodeQL py/file-not-closed",
    url: "https://codeql.github.com/codeql-query-help/python/py-file-not-closed/",
  },
];

/** Resolved callees that return an object holding an OS resource. */
const HANDLE_CALLEES = new Set<string>([
  "open",
  "io.open",
  "codecs.open",
  "gzip.open",
  "bz2.open",
  "lzma.open",
  "tarfile.open",
  "zipfile.ZipFile",
  "tempfile.TemporaryFile",
  "tempfile.NamedTemporaryFile",
  "tempfile.SpooledTemporaryFile",
  "socket.socket",
  "socket.create_connection",
  "sqlite3.connect",
  "urllib.request.urlopen",
  "subprocess.Popen",
]);

const CLOSE_METHODS = new Set(["close"]);
const POPEN_RELEASE_METHODS = new Set(["close", "terminate", "kill", "wait", "communicate"]);

/** Wrappers that are climbed through when asking "does this value flow somewhere?". */
const PASS_THROUGH = new Set([
  "tuple",
  "list",
  "set",
  "dictionary",
  "pair",
  "expression_list",
  "parenthesized_expression",
  "list_splat",
  "conditional_expression",
  "boolean_operator",
  "as_pattern",
]);

const LIMITATIONS = [
  "Static only: whether the handle is actually dropped without close, and how long it stays open, is not measured.",
  "SRC-01 did not observe this smell in its Python data; this is a reliability finding with low energy weight, not a measured energy cost.",
  "CPython reference counting usually closes a dropped file object immediately (the author's reasoning, not a cited source), so the practical cost is mostly on other interpreters or when the handle is kept alive in a cycle or traceback.",
  "Memory profilers do not track file descriptors, so no profile artifact can confirm or quantify a leak.",
];

const ws = (text: string) => text.replace(/\s+/g, "");

function enclosingFunction(node: Parser.SyntaxNode): Parser.SyntaxNode | null {
  for (let curr = node.parent; curr; curr = curr.parent) {
    if (curr.type === "function_definition") return curr;
    if (curr.type === "class_definition" || curr.type === "module") return null;
  }
  return null;
}

function handleCallee(
  call: Parser.SyntaxNode,
  aliases: ReadonlyMap<string, string>
): string | null {
  const fn = call.childForFieldName("function");
  if (!fn || (fn.type !== "identifier" && fn.type !== "attribute")) return null;
  const resolved = resolveCallee(fn.text, aliases);
  return HANDLE_CALLEES.has(resolved) ? resolved : null;
}

function inFinally(node: Parser.SyntaxNode, scope: Parser.SyntaxNode): boolean {
  for (let curr = node.parent; curr && curr.id !== scope.id; curr = curr.parent) {
    if (curr.type === "finally_clause") return true;
  }
  return false;
}

type Use = "close-safe" | "close-unsafe" | "escape" | "plain";

/** Does the value at `id` flow somewhere that takes ownership of the handle? */
function flowsAway(id: Parser.SyntaxNode): boolean {
  let child = id;
  let parent = child.parent;
  while (parent && PASS_THROUGH.has(parent.type)) {
    child = parent;
    parent = child.parent;
  }
  if (!parent) return false;
  switch (parent.type) {
    case "return_statement":
    case "yield":
    case "await":
    case "argument_list":
    case "keyword_argument":
    case "with_item":
    case "named_expression":
      return parent.type !== "keyword_argument" || parent.childForFieldName("value")?.id === child.id;
    case "assignment":
    case "augmented_assignment":
      return parent.childForFieldName("right")?.id === child.id;
    default:
      return false;
  }
}

function classifyUse(
  id: Parser.SyntaxNode,
  scope: Parser.SyntaxNode,
  release: ReadonlySet<string>
): Use {
  // A reference inside a nested def/lambda: the closure may own the handle.
  for (let curr = id.parent; curr && curr.id !== scope.id; curr = curr.parent) {
    if (curr.type === "function_definition" || curr.type === "lambda") return "escape";
  }
  const parent = id.parent;
  if (parent?.type === "attribute" && parent.childForFieldName("object")?.id === id.id) {
    const call = parent.parent;
    const method = parent.childForFieldName("attribute")?.text ?? "";
    if (call?.type === "call" && call.childForFieldName("function")?.id === parent.id && release.has(method)) {
      return inFinally(call, scope) ? "close-safe" : "close-unsafe";
    }
    return "plain";
  }
  return flowsAway(id) ? "escape" : "plain";
}

function nameUses(
  scope: Parser.SyntaxNode,
  name: string,
  skip: Parser.SyntaxNode
): Parser.SyntaxNode[] {
  const out: Parser.SyntaxNode[] = [];
  const visit = (n: Parser.SyntaxNode) => {
    if (n.type === "identifier" && n.text === name && n.id !== skip.id) {
      const p = n.parent;
      const isAttributeName = p?.type === "attribute" && p.childForFieldName("attribute")?.id === n.id;
      const isKeywordName = p?.type === "keyword_argument" && p.childForFieldName("name")?.id === n.id;
      const isRebind =
        p?.type === "assignment" && p.childForFieldName("left")?.id === n.id;
      if (!isAttributeName && !isKeywordName && !isRebind) out.push(n);
    }
    for (const c of n.namedChildren) visit(c);
  };
  const body = scope.childForFieldName("body");
  if (body) visit(body);
  return out;
}

function statementOf(node: Parser.SyntaxNode): Parser.SyntaxNode {
  let curr = node;
  while (curr.parent && curr.parent.type !== "block" && curr.parent.type !== "module") curr = curr.parent;
  return curr;
}

interface Site {
  /** The handle-producing call. */
  call: Parser.SyntaxNode;
  callee: string;
  /** Local name, or null for the chained `open(...).read()` form. */
  name: string | null;
  /** The assignment, or the chained `.method(...)` call. */
  stmt: Parser.SyntaxNode;
  fn: Parser.SyntaxNode;
  /** unsafe-close and chained forms are reported at low confidence. */
  confidence: Confidence;
  reason: "never-closed" | "close-not-in-finally" | "chained";
}

function collectSites(root: Parser.SyntaxNode): Site[] {
  const aliases = collectImportAliases(root);
  const sites: Site[] = [];

  const visit = (n: Parser.SyntaxNode) => {
    if (n.type === "assignment") {
      const left = n.childForFieldName("left");
      const right = n.childForFieldName("right");
      if (left?.type === "identifier" && right?.type === "call") {
        const callee = handleCallee(right, aliases);
        const fn = enclosingFunction(n);
        if (callee && fn) {
          const release = callee === "subprocess.Popen" ? POPEN_RELEASE_METHODS : CLOSE_METHODS;
          const uses = nameUses(fn, left.text, left).map((u) => classifyUse(u, fn, release));
          if (!uses.includes("escape") && !uses.includes("close-safe")) {
            const unsafe = uses.includes("close-unsafe");
            sites.push({
              call: right,
              callee,
              name: left.text,
              stmt: n,
              fn,
              confidence: unsafe ? "low" : "medium",
              reason: unsafe ? "close-not-in-finally" : "never-closed",
            });
          }
        }
      }
    } else if (n.type === "call") {
      // Chained form: open(p).read(), socket.create_connection(a).sendall(b), ...
      const fnNode = n.childForFieldName("function");
      const obj = fnNode?.type === "attribute" ? fnNode.childForFieldName("object") : null;
      if (obj?.type === "call") {
        const callee = handleCallee(obj, aliases);
        const method = fnNode!.childForFieldName("attribute")?.text ?? "";
        const fn = enclosingFunction(n);
        if (callee && fn && callee !== "subprocess.Popen" && method !== "close") {
          sites.push({
            call: obj,
            callee,
            name: null,
            stmt: statementOf(n),
            fn,
            confidence: "low",
            reason: "chained",
          });
        }
      }
    }
    for (const c of n.namedChildren) visit(c);
  };
  visit(root);
  return sites;
}

/**
 * Pure function detecting handles opened into a local name (or chained on the
 * call result) inside a function and never closed or context-managed (CODE-C6.6).
 * Never performs I/O or executes code.
 */
export function detectUnclosedHandles(
  rootNode: Parser.SyntaxNode,
  filePath: string,
  sourceLines: string[]
): Finding[] {
  const findings: Finding[] = [];
  const ordinals = new Map<string, number>();

  for (const s of collectSites(rootNode)) {
    const stmtRow = s.stmt.startPosition.row;
    const callRow = s.call.startPosition.row;
    if (
      [stmtRow, callRow].some(
        (r) => isLineSuppressed(sourceLines[r] ?? "", SUPPRESSION_CODES).isSuppressed
      )
    ) {
      continue;
    }

    const symbol = s.name ?? "(chained)";
    const qualname = getEnclosingQualname(s.call);
    const key = `${qualname}:${symbol}:${s.callee}`;
    const ordinal = ordinals.get(key) ?? 0;
    ordinals.set(key, ordinal + 1);

    const target = s.name ?? "f";
    const callText = ws(s.call.text).length > 60 ? `${s.callee}(...)` : s.call.text.replace(/\s+/g, " ");
    let why: string;
    if (s.reason === "never-closed") {
      why = `'${s.callee}' result is bound to '${symbol}' in '${qualname}' but is never closed or context-managed, and it does not escape the function, so the handle is held until garbage collection.`;
    } else if (s.reason === "close-not-in-finally") {
      why = `'${symbol}' from '${s.callee}' in '${qualname}' is closed, but the close is not exception-safe: it is not in a 'finally' block or 'with', so an exception before it leaks the handle.`;
    } else {
      why = `'${s.callee}(...)' in '${qualname}' is used directly (chained call) with no 'with', so nothing closes the handle explicitly.`;
    }

    const limitations = [...LIMITATIONS];
    if (s.reason === "close-not-in-finally") {
      limitations.push("The close is reached on the normal path only; whether an exception can occur before it is not analyzed.");
    }
    if (s.callee === "subprocess.Popen") {
      limitations.push("For Popen, terminate/kill/wait/communicate are accepted as release; stdout/stderr pipes are not tracked separately.");
    }

    const startRow = s.stmt.startPosition.row;
    findings.push({
      check: CHECK,
      kind: KIND,
      fingerprint: generateFingerprint(CHECK, KIND, filePath, `${key}:${ordinal}`),
      identity: `${KIND}:${key}:${ordinal}`,
      location: { path: filePath, startLine: startRow + 1, endLine: s.stmt.endPosition.row + 1 },
      evidence: {
        snippet: (sourceLines[startRow] ?? "").trim(),
        symbol,
        factory: s.callee,
        suggested: `with ${callText} as ${target}:`,
      },
      why,
      severity: "low",
      confidence: s.confidence,
      limitations,
      evidenceTier: "static",
      impact: {
        quantified: false,
        reason: "Reliability finding: leaked-handle lifetime and descriptor pressure are not measured statically, and SRC-01 did not observe this smell in Python.",
      },
      references: REFERENCES.map((r) => ({ ...r })),
      agentPrompt: `In ${filePath}:${startRow + 1}, wrap the ${s.callee} call in \`with ${callText} as ${target}:\` (or close it in a \`finally:\`) so the handle is released even on exceptions; keep the change minimal and covered by tests. Static finding only.`,
      detector: { id: "owner-a-static-scan", version: DETECTOR_VERSION },
    });
  }

  return findings.sort((a, b) => a.location.startLine - b.location.startLine);
}
