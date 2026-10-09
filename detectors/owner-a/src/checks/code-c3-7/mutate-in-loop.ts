import Parser from "tree-sitter";
import { Confidence, Finding, Severity, generateFingerprint } from "../../core/finding.js";
import { isLineSuppressed } from "../../core/suppressions.js";
import {
  LoopType,
  getEnclosingQualname,
  isNameChain,
  iteratedCollection,
  sameBaseChain,
} from "../../core/loops.js";

const CHECK = "CODE-C3.7";
const DETECTOR_VERSION = "0.1.0";

const REFERENCES = [
  {
    id: "SRC-01",
    title:
      "Watts This Smell: A Comprehensive Taxonomy of Software Energy Smells",
    url: "https://arxiv.org/abs/2604.04809",
  },
  {
    id: "taxonomy-c3.7",
    title: "Software Compute Waste Taxonomy — C3.7 Inefficient array mutation",
    url: "https://github.com/AWS-env/environmental-hacks/issues/66",
  },
];

const STATIC_ONLY =
  "Static only: trip count and collection size are not measured, so the cost of each shift or skipped element is not quantified.";
const TYPE_UNKNOWN =
  "Container type is not visible in this file; if the name is a collections.deque, front operations are O(1) and this finding does not apply.";

type Kind = "mutate-during-iteration" | "front-reindex-in-loop";
type Signal = "remove" | "front" | "grow" | "clear";

const REMOVE_METHODS = new Set(["remove", "pop", "popitem", "discard", "popleft"]);
const GROW_METHODS = new Set(["append", "extend", "insert", "add", "update", "setdefault", "appendleft"]);
const SCOPE_BOUNDARIES = new Set(["function_definition", "class_definition", "lambda", "module"]);
const LOOP_TYPES = new Set(["for_statement", "while_statement"]);
const EXIT_TYPES = new Set(["break_statement", "return_statement", "raise_statement"]);

interface Mutation {
  /** The statement-level node performing the mutation. */
  site: Parser.SyntaxNode;
  base: Parser.SyntaxNode;
  /** Label for evidence.mutator, e.g. `remove`, `pop(0)`, `del`, `slice-store`. */
  mutator: string;
  signal: Signal;
  /** pop(0) / insert(0, …) / del x[0]. */
  front: boolean;
}

const ws = (text: string) => text.replace(/\s+/g, "");

function isZero(node: Parser.SyntaxNode | undefined | null): boolean {
  return node?.type === "integer" && Number(node.text) === 0;
}

function positionalArgs(call: Parser.SyntaxNode): Parser.SyntaxNode[] {
  const args = call.childForFieldName("arguments");
  return args ? args.namedChildren.filter((c) => c.type !== "comment" && c.type !== "keyword_argument") : [];
}

/** Every mutation of a name-chain collection anywhere in the file. */
function collectMutations(root: Parser.SyntaxNode): Mutation[] {
  const out: Mutation[] = [];
  const visit = (n: Parser.SyntaxNode) => {
    if (n.type === "call") {
      const fn = n.childForFieldName("function");
      const obj = fn?.type === "attribute" ? fn.childForFieldName("object") : null;
      const method = fn?.type === "attribute" ? fn.childForFieldName("attribute")?.text ?? "" : "";
      if (obj && isNameChain(obj)) {
        const first = positionalArgs(n)[0];
        if (REMOVE_METHODS.has(method)) {
          const front = method === "pop" && isZero(first);
          out.push({ site: n, base: obj, mutator: front ? "pop(0)" : method, signal: front ? "front" : "remove", front });
        } else if (GROW_METHODS.has(method)) {
          const front = method === "insert" && isZero(first);
          out.push({ site: n, base: obj, mutator: front ? "insert(0)" : method, signal: front ? "front" : "grow", front });
        } else if (method === "clear" && !first) {
          out.push({ site: n, base: obj, mutator: "clear", signal: "clear", front: false });
        }
      }
    } else if (n.type === "delete_statement") {
      const targets = n.namedChildren.flatMap((c) => (c.type === "expression_list" ? c.namedChildren : [c]));
      for (const t of targets) {
        const value = t.type === "subscript" ? t.childForFieldName("value") : null;
        if (value && isNameChain(value)) {
          const front = isZero(t.childForFieldName("subscript"));
          out.push({ site: n, base: value, mutator: front ? "del [0]" : "del", signal: front ? "front" : "remove", front });
        }
      }
    } else if (n.type === "assignment") {
      const left = n.childForFieldName("left");
      const value = left?.type === "subscript" ? left.childForFieldName("value") : null;
      if (value && isNameChain(value) && left!.childForFieldName("subscript")?.type === "slice") {
        out.push({ site: n, base: value, mutator: "slice-store", signal: "clear", front: false });
      }
    } else if (n.type === "augmented_assignment") {
      const left = n.childForFieldName("left");
      const op = n.childForFieldName("operator")?.text;
      if (left && isNameChain(left) && op === "+=") {
        out.push({ site: n, base: left, mutator: "+=", signal: "grow", front: false });
      }
    }
    for (const child of n.namedChildren) visit(child);
  };
  visit(root);
  return out;
}

/** Enclosing loops of `node`, innermost first, stopping at the enclosing def/class/lambda. */
function enclosingLoops(node: Parser.SyntaxNode): Parser.SyntaxNode[] {
  const loops: Parser.SyntaxNode[] = [];
  let child = node;
  for (let curr = node.parent; curr && !SCOPE_BOUNDARIES.has(curr.type); child = curr, curr = curr.parent) {
    // Only the body counts: mutations in a `for` header or `while` condition are not "in" the loop.
    if (LOOP_TYPES.has(curr.type) && curr.childForFieldName("body")?.id === child.id) loops.push(curr);
  }
  return loops;
}

function statementOf(node: Parser.SyntaxNode): Parser.SyntaxNode {
  let curr = node;
  while (curr.parent && curr.parent.type !== "block" && curr.parent.type !== "module") curr = curr.parent;
  return curr;
}

/**
 * The mutation is followed, in its own block, by an exit that leaves `loop`
 * (`break` owned by it, or `return` / `raise`): it runs at most once per loop,
 * so it neither skips elements nor shifts repeatedly.
 */
function exitsRightAfter(site: Parser.SyntaxNode, loop: Parser.SyntaxNode): boolean {
  const stmt = statementOf(site);
  for (let next = stmt.nextNamedSibling; next; next = next.nextNamedSibling) {
    if (!EXIT_TYPES.has(next.type)) continue;
    if (next.type !== "break_statement") return true;
    return enclosingLoops(next)[0]?.id === loop.id;
  }
  return false;
}

function functionScope(node: Parser.SyntaxNode): Parser.SyntaxNode {
  let curr = node.parent;
  while (curr && curr.type !== "function_definition" && curr.type !== "module") curr = curr.parent;
  return curr ?? node;
}

type Binding = "deque" | "list" | "unknown";

/**
 * What `base` is bound to: an assignment in the enclosing function (or, for a
 * `self.x` chain, anywhere in the file), or a parameter annotation.
 */
function bindingOf(base: Parser.SyntaxNode, root: Parser.SyntaxNode): Binding {
  const key = ws(base.text);
  const scope = base.type === "identifier" ? functionScope(base) : root;
  let found: Binding = "unknown";
  const classify = (value: Parser.SyntaxNode | null): Binding => {
    if (!value) return "unknown";
    if (value.type === "list" || value.type === "list_comprehension") return "list";
    if (value.type === "call") {
      const fn = value.childForFieldName("function")?.text ?? "";
      if (fn === "deque" || fn.endsWith(".deque")) return "deque";
      if (fn === "list") return "list";
    }
    return "unknown";
  };
  const classifyType = (type: Parser.SyntaxNode | null): Binding => {
    const t = type?.text ?? "";
    if (/\bdeque\b|\bDeque\b/.test(t)) return "deque";
    if (/^(list|List)\b/.test(t)) return "list";
    return "unknown";
  };
  const visit = (n: Parser.SyntaxNode) => {
    if (n.type === "assignment") {
      const left = n.childForFieldName("left");
      if (left && ws(left.text) === key) {
        const b = classify(n.childForFieldName("right"));
        const annotated = classifyType(n.childForFieldName("type"));
        const result = annotated !== "unknown" ? annotated : b;
        if (result === "deque") found = "deque";
        else if (result === "list" && found === "unknown") found = "list";
      }
    } else if (n.type === "typed_parameter" || n.type === "typed_default_parameter") {
      const name = n.namedChildren.find((c) => c.type === "identifier");
      if (name?.text === key) {
        const result = classifyType(n.childForFieldName("type"));
        if (result === "deque") found = "deque";
        else if (result === "list" && found === "unknown") found = "list";
      }
    }
    for (const child of n.namedChildren) visit(child);
  };
  visit(scope);
  return found;
}

interface Match {
  kind: Kind;
  loop: Parser.SyntaxNode;
  severity: Severity;
  confidence: Confidence;
}

function classify(m: Mutation, root: Parser.SyntaxNode): Match | null {
  const loops = enclosingLoops(m.site);
  if (loops.length === 0) return null;

  // S1/S3/S4: a `for` loop (innermost first) that iterates this exact collection.
  const iterating = loops.find((l) => l.type === "for_statement" && sameBaseChain(iteratedCollection(l), m.base));
  if (iterating) {
    if (exitsRightAfter(m.site, iterating)) return null;
    if (m.signal === "remove" || m.signal === "front") {
      return { kind: "mutate-during-iteration", loop: iterating, severity: "high", confidence: "high" };
    }
    if (m.signal === "clear") {
      return { kind: "mutate-during-iteration", loop: iterating, severity: "medium", confidence: "medium" };
    }
    return { kind: "mutate-during-iteration", loop: iterating, severity: "low", confidence: "medium" };
  }

  // S2: front re-indexing on a list inside any loop.
  if (!m.front) return null;
  const loop = loops[0];
  if (exitsRightAfter(m.site, loop)) return null;
  const binding = bindingOf(m.base, root);
  if (binding === "deque") return null;
  return {
    kind: "front-reindex-in-loop",
    loop,
    severity: "high",
    confidence: binding === "list" ? "high" : "medium",
  };
}

function whyFor(m: Mutation, match: Match, base: string, header: string): string {
  if (match.kind === "front-reindex-in-loop") {
    return `'${base}.${m.mutator}' inside loop '${header}' shifts every remaining element on each iteration, so draining or filling a list from the front is O(n²).`;
  }
  switch (m.signal) {
    case "remove":
    case "front":
      return `Loop '${header}' removes from '${base}' while iterating it: on a list this skips the element after each removal and shifts the tail (O(n) per removal); on a dict or set it raises RuntimeError.`;
    case "clear":
      return `Loop '${header}' changes the length of '${base}' (${m.mutator}) while iterating it; the iterator's position no longer matches the contents, so elements are skipped or the dict/set raises RuntimeError.`;
    case "grow":
      return `Loop '${header}' grows '${base}' (${m.mutator}) while iterating it; a list keeps iterating the new elements (an implicit worklist) and a dict or set raises RuntimeError.`;
  }
}

function promptFor(m: Mutation, match: Match, base: string): string {
  if (match.kind === "front-reindex-in-loop") {
    return m.mutator === "insert(0)"
      ? `build the result with \`append\` and reverse once after the loop, or make '${base}' a \`collections.deque\` and use \`appendleft\``
      : `make '${base}' a \`collections.deque\` and use \`popleft()\`, or iterate the list once by index instead of popping from the front`;
  }
  switch (m.signal) {
    case "remove":
    case "front":
      return `build a new collection instead of removing in place (list: \`${base}[:] = [x for x in ${base} if keep(x)]\`; dict: \`${base} = {k: v for k, v in ${base}.items() if keep(k, v)}\`), or iterate over a copy (\`for x in list(${base}):\`)`;
    case "clear":
      return `iterate over a copy (\`for x in list(${base}):\`) or compute the replacement after the loop`;
    case "grow":
      return `if '${base}' is meant as a worklist, make it explicit (\`collections.deque\` with \`while ${base}: item = ${base}.popleft()\`); otherwise collect additions in a separate list and extend after the loop`;
  }
}

/**
 * Pure function detecting collections mutated inside the loop that iterates them,
 * and front re-indexing of lists inside loops (CODE-C3.7). Never performs I/O or executes code.
 */
export function detectArrayMutation(
  rootNode: Parser.SyntaxNode,
  filePath: string,
  sourceLines: string[]
): Finding[] {
  const findings: Finding[] = [];
  const ordinals = new Map<string, number>();

  for (const m of collectMutations(rootNode)) {
    const match = classify(m, rootNode);
    if (!match) continue;

    const loopRow = match.loop.startPosition.row;
    const siteRow = m.site.startPosition.row;
    if ([loopRow, siteRow].some((r) => isLineSuppressed(sourceLines[r] ?? "", [CHECK]).isSuppressed)) {
      continue;
    }

    const base = ws(m.base.text);
    const qualname = getEnclosingQualname(m.site);
    const key = `${qualname}:${base}:${m.mutator}`;
    const ordinal = ordinals.get(key) ?? 0;
    ordinals.set(key, ordinal + 1);
    const id = `${match.kind}:${key}:${ordinal}`;

    const header = (sourceLines[loopRow] ?? "").trim().replace(/:$/, "");
    const loopType: LoopType = match.loop.type === "for_statement" ? "for" : "while";
    const limitations = [STATIC_ONLY];
    if (match.kind === "front-reindex-in-loop" && match.confidence === "medium") limitations.push(TYPE_UNKNOWN);
    if (match.kind === "mutate-during-iteration" && m.signal === "grow") {
      limitations.push("Appending to a list while iterating it is a common intentional worklist; it is a correctness risk, not a re-indexing cost (append is amortized O(1)).");
    }

    findings.push({
      check: CHECK,
      kind: match.kind,
      fingerprint: generateFingerprint(CHECK, match.kind, filePath, `${key}:${ordinal}`),
      identity: id,
      location: { path: filePath, startLine: siteRow + 1, endLine: m.site.endPosition.row + 1 },
      evidence: {
        snippet: `${header}:\n${(sourceLines[siteRow] ?? "").trim()}`,
        symbol: base,
        mutator: m.mutator,
        loopType,
      },
      why: whyFor(m, match, base, header),
      severity: match.severity,
      confidence: match.confidence,
      limitations,
      evidenceTier: "static",
      impact: {
        quantified: false,
        reason: "Cost = shifts or skipped elements × trip count; neither is measured statically.",
      },
      references: REFERENCES.map((r) => ({ ...r })),
      agentPrompt: `In ${filePath}:${siteRow + 1} (loop at line ${loopRow + 1}), ${promptFor(m, match, base)}. Static finding only — keep the change minimal and covered by tests.`,
      detector: { id: "owner-a-static-scan", version: DETECTOR_VERSION },
    });
  }

  return findings.sort((a, b) => a.location.startLine - b.location.startLine);
}
