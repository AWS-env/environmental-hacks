import Parser from "tree-sitter";
import { Confidence, Finding, generateFingerprint } from "../../core/finding.js";
import { isLineSuppressed } from "../../core/suppressions.js";
import { checkRunsAtMostOnce, getEnclosingQualname } from "../../core/loops.js";

const CHECK = "CODE-C10.4";
const KIND = "string-concat-in-loop";
const DETECTOR_VERSION = "0.1.0";
/** Loops over at most this many constant items are not flagged. */
const MAX_CONSTANT_ITEMS = 8;

const REFERENCES = [
  {
    id: "SRC-01",
    title: "Watts This Smell: A Comprehensive Taxonomy of Software Energy Smells",
    url: "https://arxiv.org/abs/2604.04809",
  },
  {
    id: "taxonomy-c10.4",
    title: "Software Compute Waste Taxonomy - C10.4 Inefficient string concatenation",
    url: "https://github.com/AWS-env/environmental-hacks/issues/45",
  },
  {
    id: "amazonq-python-string-concatenation",
    title: "Amazon Q detector library: python/string-concatenation",
    url: "http://docs.aws.amazon.com/amazonq/detector-library/python/string-concatenation/",
  },
  {
    id: "sourcery-use-join",
    title: "Sourcery rule: use-join",
    url: "https://docs.sourcery.ai/References/Sourcery-Rules/Python/Default-Rules/use-join/",
  },
];

const STATIC_ONLY =
  "Static only: the loop trip count is not measured and the saving is bounded (it grows with the number and size of the pieces), so no saving is quantified.";
const LOCAL_CAVEAT =
  "CPython may resize a local str in place when it holds the only reference, which can make `+=` on a local roughly linear; this optimisation is UNVERIFIED from an official page, and other implementations do not have it. Confidence is therefore low for local names.";

const SCOPE_BOUNDARIES = new Set(["function_definition", "class_definition", "lambda", "module"]);
const LOOP_TYPES = new Set(["for_statement", "while_statement"]);
const EXIT_TYPES = new Set(["break_statement", "return_statement", "raise_statement"]);
const OTHER_VALUE_TYPES = new Set([
  "integer",
  "float",
  "list",
  "tuple",
  "dictionary",
  "set",
  "list_comprehension",
  "set_comprehension",
  "dictionary_comprehension",
  "generator_expression",
  "true",
  "false",
  "none",
]);

type ValueClass = "string" | "other" | "unknown";

const ws = (text: string) => text.replace(/\s+/g, "");

function stripParens(node: Parser.SyntaxNode | null): Parser.SyntaxNode | null {
  let n = node;
  while (n?.type === "parenthesized_expression" && n.namedChildren.length === 1) n = n.namedChildren[0];
  return n;
}

function opOf(node: Parser.SyntaxNode): string {
  return node.childForFieldName("operator")?.text ?? "";
}

function isBytesLiteral(node: Parser.SyntaxNode): boolean {
  const first = node.type === "concatenated_string" ? node.namedChildren[0] : node;
  return /^[a-zA-Z]*[bB][a-zA-Z]*['"]/.test(first?.text ?? "");
}

/** Classify a value expression: str, a non-str built-in (number/list/tuple/bytes/...), or unknown. */
function classifyValue(node: Parser.SyntaxNode | null): ValueClass {
  const n = stripParens(node);
  if (!n) return "unknown";
  if (n.type === "string" || n.type === "concatenated_string") return isBytesLiteral(n) ? "other" : "string";
  if (OTHER_VALUE_TYPES.has(n.type)) return "other";
  if (n.type === "unary_operator") {
    const arg = n.childForFieldName("argument");
    return arg && (arg.type === "integer" || arg.type === "float") ? "other" : "unknown";
  }
  if (n.type === "call") {
    const fn = n.childForFieldName("function");
    if (fn?.type === "identifier") {
      if (fn.text === "str") return "string";
      if (["list", "tuple", "bytes", "bytearray", "int", "float", "dict", "set"].includes(fn.text)) return "other";
    }
    if (fn?.type === "attribute") {
      const obj = stripParens(fn.childForFieldName("object"));
      const method = fn.childForFieldName("attribute")?.text;
      if ((method === "join" || method === "format") && obj && classifyValue(obj) === "string") return "string";
    }
    return "unknown";
  }
  if (n.type === "binary_operator") {
    const op = opOf(n);
    if (op === "%" && classifyValue(n.childForFieldName("left")) === "string") return "string";
    if (op === "+") {
      const l = classifyValue(n.childForFieldName("left"));
      const r = classifyValue(n.childForFieldName("right"));
      if (l === "string" || r === "string") return "string";
      if (l === "other" || r === "other") return "other";
    }
  }
  return "unknown";
}

function isAnnotatedStr(type: Parser.SyntaxNode | null): boolean {
  return (type?.text ?? "").trim() === "str";
}

interface Accum {
  stmt: Parser.SyntaxNode;
  target: Parser.SyntaxNode;
  /** Nodes that are the accumulator itself (target, and the leading `NAME` of `NAME = NAME + ...`). */
  selfNodes: Parser.SyntaxNode[];
  operands: Parser.SyntaxNode[];
  form: "+=" | "= +";
}

/** `NAME += EXPR` or `NAME = NAME + EXPR [+ ...]` for a name, attribute or subscript target. */
function accumulation(stmt: Parser.SyntaxNode): Accum | null {
  const left = stmt.childForFieldName("left");
  const right = stmt.childForFieldName("right");
  if (!left || !right) return null;
  if (!["identifier", "attribute", "subscript"].includes(left.type)) return null;
  if (stmt.type === "augmented_assignment") {
    if (opOf(stmt) !== "+=") return null;
    return { stmt, target: left, selfNodes: [left], operands: [right], form: "+=" };
  }
  if (stmt.type === "assignment" && right.type === "binary_operator") {
    const operands: Parser.SyntaxNode[] = [];
    let cur: Parser.SyntaxNode = right;
    while (cur.type === "binary_operator" && opOf(cur) === "+") {
      const r = cur.childForFieldName("right");
      const l = cur.childForFieldName("left");
      if (!r || !l) return null;
      operands.unshift(r);
      cur = l;
    }
    if (operands.length === 0 || ws(cur.text) !== ws(left.text)) return null;
    return { stmt, target: left, selfNodes: [left, cur], operands, form: "= +" };
  }
  return null;
}

/** Enclosing loops of `node`, innermost first, stopping at the enclosing def/class/lambda. */
function enclosingLoops(node: Parser.SyntaxNode): Parser.SyntaxNode[] {
  const loops: Parser.SyntaxNode[] = [];
  let child = node;
  for (let curr = node.parent; curr && !SCOPE_BOUNDARIES.has(curr.type); child = curr, curr = curr.parent) {
    if (LOOP_TYPES.has(curr.type) && curr.childForFieldName("body")?.id === child.id) loops.push(curr);
  }
  return loops;
}

function statementOf(node: Parser.SyntaxNode): Parser.SyntaxNode {
  let curr = node;
  while (curr.parent && curr.parent.type !== "block" && curr.parent.type !== "module") curr = curr.parent;
  return curr;
}

/** `loop` is the nearest loop owning a break found at the same block level after `stmt`. */
function exitsRightAfter(stmt: Parser.SyntaxNode, loop: Parser.SyntaxNode): boolean {
  const st = statementOf(stmt);
  for (let next = st.nextNamedSibling; next; next = next.nextNamedSibling) {
    if (!EXIT_TYPES.has(next.type)) continue;
    if (next.type !== "break_statement") return true;
    return enclosingLoops(next)[0]?.id === loop.id;
  }
  return false;
}

function isConstantItem(node: Parser.SyntaxNode): boolean {
  const n = stripParens(node);
  if (!n) return false;
  if (["integer", "float", "true", "false", "none"].includes(n.type)) return true;
  if (n.type === "string") return !n.namedChildren.some((c) => c.type === "interpolation");
  if (n.type === "unary_operator") {
    const arg = n.childForFieldName("argument");
    return arg?.type === "integer" || arg?.type === "float";
  }
  return false;
}

const intLiteral = (node: Parser.SyntaxNode | undefined): number | null =>
  node && /^-?\s*\d+$/.test(node.text) && ["integer", "unary_operator"].includes(node.type)
    ? Number(node.text.replace(/\s+/g, ""))
    : null;

/** A `for` over a literal collection or `range(<int literals>)` of at most MAX_CONSTANT_ITEMS items. */
function isSmallConstantLoop(loop: Parser.SyntaxNode): boolean {
  if (loop.type !== "for_statement") return false;
  const iter = stripParens(loop.childForFieldName("right"));
  if (!iter) return false;
  if (["list", "tuple", "set", "expression_list"].includes(iter.type)) {
    const items = iter.namedChildren.filter((c) => c.type !== "comment");
    return items.length <= MAX_CONSTANT_ITEMS && items.every(isConstantItem);
  }
  if (iter.type === "call" && iter.childForFieldName("function")?.text === "range") {
    const args = iter.childForFieldName("arguments")?.namedChildren.filter((c) => c.type !== "comment") ?? [];
    if (args.length < 1 || args.length > 3) return false;
    const nums = args.map(intLiteral);
    if (nums.some((v) => v === null)) return false;
    const [a, b, c] = nums as number[];
    const [start, stop, step] = args.length === 1 ? [0, a, 1] : [a, b, c ?? 1];
    if (step === 0) return false;
    const count = Math.max(0, Math.ceil((stop - start) / step));
    return count <= MAX_CONSTANT_ITEMS;
  }
  return false;
}

/** The first enclosing loop (innermost first) that can actually repeat. */
function repeatingLoop(stmt: Parser.SyntaxNode): Parser.SyntaxNode | null {
  for (const loop of enclosingLoops(stmt)) {
    if (exitsRightAfter(stmt, loop)) continue;
    if (checkRunsAtMostOnce(loop.childForFieldName("body"))) continue;
    if (isSmallConstantLoop(loop)) continue;
    return loop;
  }
  return null;
}

function functionScope(node: Parser.SyntaxNode): Parser.SyntaxNode {
  let curr = node.parent;
  while (curr && curr.type !== "function_definition" && curr.type !== "module") curr = curr.parent;
  return curr ?? node;
}

function declaredGlobal(scope: Parser.SyntaxNode, name: string): boolean {
  let found = false;
  const visit = (n: Parser.SyntaxNode) => {
    if (n.id !== scope.id && (n.type === "function_definition" || n.type === "class_definition" || n.type === "lambda")) return;
    if (n.type === "global_statement" && n.namedChildren.some((c) => c.text === name)) found = true;
    for (const c of n.namedChildren) visit(c);
  };
  visit(scope);
  return found;
}

function assignmentClass(assign: Parser.SyntaxNode): ValueClass {
  if (isAnnotatedStr(assign.childForFieldName("type"))) return "string";
  return classifyValue(assign.childForFieldName("right"));
}

/** Binding of a local name: the last assignment before the loop in the same function, else a `str` parameter. */
function localBinding(name: string, scope: Parser.SyntaxNode, loop: Parser.SyntaxNode): ValueClass {
  let last: Parser.SyntaxNode | null = null;
  let param: ValueClass = "unknown";
  const visit = (n: Parser.SyntaxNode) => {
    if (n.id !== scope.id && (n.type === "function_definition" || n.type === "class_definition" || n.type === "lambda")) return;
    if (n.type === "assignment" && n.endIndex <= loop.startIndex) {
      const left = n.childForFieldName("left");
      if (left?.type === "identifier" && left.text === name) {
        if (!last || n.startIndex > last.startIndex) last = n;
      }
    } else if ((n.type === "typed_parameter" || n.type === "typed_default_parameter") && n.parent?.parent?.id === scope.id) {
      const id = n.namedChildren.find((c) => c.type === "identifier");
      if (id?.text === name && isAnnotatedStr(n.childForFieldName("type"))) param = "string";
    }
    for (const c of n.namedChildren) visit(c);
  };
  visit(scope);
  return last ? assignmentClass(last) : param;
}

/** Binding of an attribute/subscript/global chain: every assignment to the same chain in the file. */
function fileBinding(key: string, root: Parser.SyntaxNode): ValueClass {
  const classes = new Set<ValueClass>();
  const visit = (n: Parser.SyntaxNode) => {
    if (n.type === "assignment") {
      const left = n.childForFieldName("left");
      if (left && ws(left.text) === key) classes.add(assignmentClass(n));
    }
    for (const c of n.namedChildren) visit(c);
  };
  visit(root);
  if (classes.has("other")) return "other";
  return classes.has("string") ? "string" : "unknown";
}

/** Every assignment-style statement in a loop body (not entering nested defs). */
function walkLoopBody(loop: Parser.SyntaxNode, fn: (n: Parser.SyntaxNode) => void): void {
  const body = loop.childForFieldName("body");
  if (!body) return;
  const visit = (n: Parser.SyntaxNode) => {
    if (n.type === "function_definition" || n.type === "class_definition") return;
    fn(n);
    for (const c of n.namedChildren) visit(c);
  };
  visit(body);
}

interface LoopUse {
  reassigned: boolean;
  read: boolean;
}

/** Whether the accumulator is reset or read in the loop body besides the accumulating statements. */
function loopUse(loop: Parser.SyntaxNode, key: string): LoopUse {
  const selfIds = new Set<number>();
  let reassigned = false;
  walkLoopBody(loop, (n) => {
    if (n.type !== "assignment" && n.type !== "augmented_assignment") return;
    const left = n.childForFieldName("left");
    if (!left || ws(left.text) !== key) return;
    const acc = accumulation(n);
    if (acc) acc.selfNodes.forEach((s) => selfIds.add(s.id));
    else if (n.type === "assignment") reassigned = true;
  });
  const loopTarget = loop.type === "for_statement" ? loop.childForFieldName("left") : null;
  if (loopTarget && ws(loopTarget.text).split(/[,()\[\]]/).includes(key)) reassigned = true;

  let read = false;
  walkLoopBody(loop, (n) => {
    if (read || selfIds.has(n.id)) return;
    const matches =
      key.includes(".") || key.includes("[")
        ? (n.type === "attribute" || n.type === "subscript") && ws(n.text) === key
        : n.type === "identifier" && n.text === key;
    if (!matches) return;
    const p = n.parent;
    if (p?.type === "attribute" && p.childForFieldName("attribute")?.id === n.id) return;
    if (p?.type === "keyword_argument" && p.childForFieldName("name")?.id === n.id) return;
    if ((p?.type === "assignment" || p?.type === "augmented_assignment") && p.childForFieldName("left")?.id === n.id) return;
    read = true;
  });
  return { reassigned, read };
}

function collectStatements(root: Parser.SyntaxNode): Parser.SyntaxNode[] {
  const out: Parser.SyntaxNode[] = [];
  const visit = (n: Parser.SyntaxNode) => {
    if (n.type === "augmented_assignment" || n.type === "assignment") out.push(n);
    for (const c of n.namedChildren) visit(c);
  };
  visit(root);
  return out;
}

/**
 * Pure function detecting `s += x` / `s = s + x` string accumulation inside for/while loops
 * (CODE-C10.4). Never performs I/O or executes code.
 */
export function detectStringConcatInLoop(
  rootNode: Parser.SyntaxNode,
  filePath: string,
  sourceLines: string[]
): Finding[] {
  const findings: Finding[] = [];
  const ordinals = new Map<string, number>();

  for (const stmt of collectStatements(rootNode)) {
    const acc = accumulation(stmt);
    if (!acc) continue;
    const loop = repeatingLoop(stmt);
    if (!loop) continue;

    const key = ws(acc.target.text);
    const exprClasses = acc.operands.map((o) => classifyValue(o));
    // Numbers, lists, tuples, bytes: not string concatenation.
    if (exprClasses.includes("other")) continue;
    const exprIsString = exprClasses.includes("string");

    let confidence: Confidence;
    let local = false;
    if (acc.target.type === "identifier") {
      const scope = functionScope(stmt);
      const isGlobal = scope.type === "module" || declaredGlobal(scope, key);
      const binding = isGlobal ? fileBinding(key, rootNode) : localBinding(key, scope, loop);
      if (binding !== "string") continue;
      confidence = isGlobal ? "medium" : "low";
      local = !isGlobal;
    } else {
      const binding = fileBinding(key, rootNode);
      if (binding === "other") continue;
      if (binding !== "string" && !exprIsString) continue;
      confidence = "medium";
    }

    const use = loopUse(loop, key);
    if (use.reassigned || use.read) continue;

    const loopRow = loop.startPosition.row;
    const stmtRow = stmt.startPosition.row;
    if ([loopRow, stmtRow].some((r) => isLineSuppressed(sourceLines[r] ?? "", [CHECK]).isSuppressed)) continue;

    const qualname = getEnclosingQualname(stmt);
    const ordKey = `${qualname}:${key}`;
    const ordinal = ordinals.get(ordKey) ?? 0;
    ordinals.set(ordKey, ordinal + 1);
    const identity = `${KIND}:${qualname}:${key}:${ordinal}`;

    const header = (sourceLines[loopRow] ?? "").trim().replace(/:$/, "");
    const loopType = loop.type === "for_statement" ? "for" : "while";
    const limitations = [STATIC_ONLY];
    if (local) limitations.push(LOCAL_CAVEAT);

    findings.push({
      check: CHECK,
      kind: KIND,
      fingerprint: generateFingerprint(CHECK, KIND, filePath, ordKey + ":" + ordinal),
      identity,
      location: { path: filePath, startLine: stmtRow + 1, endLine: stmt.endPosition.row + 1 },
      evidence: {
        snippet: `${header}:\n${(sourceLines[stmtRow] ?? "").trim()}`,
        symbol: key,
        mutator: acc.form,
        loopType,
        suggested: `"".join(parts)`,
      },
      why: `Loop '${header}' builds the string '${key}' with repeated ${acc.form === "+=" ? "+=" : `'${key} = ${key} + ...'`}; each step creates a new string, so total copying can grow with the square of the final length instead of linearly.`,
      severity: "low",
      confidence,
      limitations,
      evidenceTier: "static",
      impact: {
        quantified: false,
        reason: "Cost depends on trip count and piece sizes, which are not measured statically.",
      },
      references: REFERENCES.map((r) => ({ ...r })),
      agentPrompt: `In ${filePath}:${stmtRow + 1} (loop at line ${loopRow + 1}), collect the pieces of '${key}' in a list inside the loop and build the string once after it with \`"".join(parts)\`. Static finding only - keep the change minimal and covered by tests.`,
      detector: { id: "owner-a-static-scan", version: DETECTOR_VERSION },
    });
  }

  return findings.sort((a, b) => a.location.startLine - b.location.startLine);
}
