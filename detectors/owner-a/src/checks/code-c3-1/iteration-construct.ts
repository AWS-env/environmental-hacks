import Parser from "tree-sitter";
import {
  Confidence,
  Finding,
  generateFingerprint,
} from "../../core/finding.js";
import { isLineSuppressed } from "../../core/suppressions.js";

const CHECK = "CODE-C3.1";
const KIND = "inefficient-iteration-construct";
const DETECTOR_VERSION = "0.1.0";

const SRC01_REFERENCE = {
  id: "SRC-01",
  title:
    "Watts This Smell: An Empirical Study on Energy Smells in Python Software",
  url: "https://arxiv.org/abs/2604.04809",
};

/** Static-only findings never claim measured savings; the R2 profiler half confirms hotness. */
const STATIC_LIMITATIONS = [
  "Loop form is syntactic only; runtime payoff is engine-dependent (CPython versus other runtimes) — measure before changing.",
  "Unconfirmed statically: no profiler evidence that this loop is hot. R2 confirmation via the owner-a-profile-parser join is a follow-up.",
];

const STATIC_IMPACT_REASON =
  "Static analysis identifies a convertible loop form; actual energy savings depend on trip count, loop-body cost, and runtime engine, none of which are measured here.";

/** Method calls that mutate the receiver collection (C3.7 owns such loops, not C3.1). */
const MUTATING_METHODS = new Set([
  "append",
  "extend",
  "insert",
  "remove",
  "pop",
  "clear",
  "sort",
  "reverse",
  "update",
  "setdefault",
  "popitem",
  "discard",
  "add",
  "difference_update",
  "intersection_update",
  "symmetric_difference_update",
]);

/** Control-flow / suspension nodes that block a mechanical comprehension rewrite (S3 only). */
const COMPREHENSION_BLOCKERS = new Set([
  "break_statement",
  "continue_statement",
  "return_statement",
  "yield",
  "await",
  "try_statement",
]);

type Signal =
  | "range-len-indexing"
  | "manual-index-while"
  | "append-accumulation"
  | "append-accumulation-gated"
  | "dict-key-lookup";

interface LoopMatch {
  signal: Signal;
  /** Collection / accumulator name for evidence.symbol */
  symbol: string;
  loopType: "for" | "while";
  suggested: string;
  why: string;
  agentPromptAction: string;
  confidence: Confidence;
  /** Signal-specific caveats appended to the shared static limitations. */
  extraLimitations?: string[];
  /** Extra 0-based rows whose `# noqa` also suppresses (e.g. the S3 append line). */
  suppressionRows?: number[];
}

// ---------------------------------------------------------------------------
// Small tree helpers
// ---------------------------------------------------------------------------

function namedChildrenSkippingComments(
  node: Parser.SyntaxNode
): Parser.SyntaxNode[] {
  return node.namedChildren.filter((c) => c.type !== "comment");
}

/** Previous sibling statement in document order, skipping comments. */
function previousSibling(
  node: Parser.SyntaxNode
): Parser.SyntaxNode | null {
  const parent = node.parent;
  if (!parent) return null;
  let prev: Parser.SyntaxNode | null = null;
  for (const sib of namedChildrenSkippingComments(parent)) {
    if (sib.startIndex >= node.startIndex) break;
    prev = sib;
  }
  return prev;
}

/** Unwrap `expr;` → inner statement node, else null. */
function unwrapExpressionStatement(
  node: Parser.SyntaxNode
): Parser.SyntaxNode | null {
  if (node.type !== "expression_statement") return null;
  const inner = namedChildrenSkippingComments(node);
  return inner.length === 1 ? inner[0] : null;
}

/** `len(X)` call → argument text, else null. */
function matchLenCall(node: Parser.SyntaxNode): string | null {
  if (node.type !== "call") return null;
  const fn = node.childForFieldName("function");
  if (!fn || fn.text !== "len") return null;
  const args = node.childForFieldName("arguments");
  if (!args) return null;
  const named = namedChildrenSkippingComments(args);
  if (named.length !== 1) return null;
  return named[0].text;
}

/** Anonymous operator child, e.g. "<", "+=", "+". */
function hasOperator(node: Parser.SyntaxNode, op: string): boolean {
  return node.children.some((c) => !c.isNamed && c.type === op);
}

/** Walk all descendant nodes (depth-first, includes self). */
function* walk(node: Parser.SyntaxNode): Generator<Parser.SyntaxNode> {
  yield node;
  for (const child of node.namedChildren) {
    yield* walk(child);
  }
}

/** Enclosing `def`/`class` qualname for fingerprint stability, else `<module>`. */
function enclosingScopeName(node: Parser.SyntaxNode): string {
  const parts: string[] = [];
  let curr: Parser.SyntaxNode | null = node.parent;
  while (curr) {
    if (
      curr.type === "function_definition" ||
      curr.type === "class_definition"
    ) {
      const name = curr.childForFieldName("name");
      if (name) parts.unshift(name.text);
    }
    curr = curr.parent;
  }
  return parts.length > 0 ? parts.join(".") : "<module>";
}

// ---------------------------------------------------------------------------
// Shared guards
// ---------------------------------------------------------------------------

/**
 * True when the loop body mutates the iterated collection `root`
 * (assignment / augmented assignment / `del` / mutating method call).
 * Such loops belong to C3.7, not C3.1.
 */
function mutatesCollection(
  body: Parser.SyntaxNode,
  root: string
): boolean {
  const wordPattern = new RegExp(`\\b${escapeRegExp(root)}\\b`);
  for (const node of walk(body)) {
    if (node.type === "assignment" || node.type === "augmented_assignment") {
      const left = node.childForFieldName("left");
      if (left && isAssignmentTargetOf(left, root)) return true;
    } else if (node.type === "delete_statement") {
      if (wordPattern.test(node.text)) return true;
    } else if (node.type === "call") {
      const fn = node.childForFieldName("function");
      if (fn && fn.type === "attribute") {
        const obj = fn.childForFieldName("object");
        const attrName = fn.namedChildren[fn.namedChildren.length - 1];
        if (
          obj &&
          obj.text === root &&
          attrName &&
          MUTATING_METHODS.has(attrName.text)
        ) {
          return true;
        }
      }
    }
  }
  return false;
}

function isAssignmentTargetOf(
  left: Parser.SyntaxNode,
  root: string
): boolean {
  if (left.type === "identifier") return left.text === root;
  if (left.type === "subscript") {
    const value = left.childForFieldName("value");
    return value !== null && value.text === root;
  }
  if (left.type === "attribute") {
    const obj = left.childForFieldName("object");
    return obj !== null && obj.text === root;
  }
  if (left.type === "tuple_pattern" || left.type === "list_pattern") {
    return namedChildrenSkippingComments(left).some((el) =>
      isAssignmentTargetOf(el, root)
    );
  }
  // Anything else (e.g. starred patterns) — stay conservative and say no.
  return false;
}

/** `async for` loops need `async for` inside the suggested comprehension. */
function isAsyncFor(loop: Parser.SyntaxNode): boolean {
  return loop.children.some((c) => !c.isNamed && c.type === "async");
}

const LOOP_OR_SCOPE = new Set([
  "for_statement",
  "while_statement",
  "function_definition",
  "class_definition",
]);

/** Statements of these types inside `body` that belong to this loop (not nested loops/defs). */
function ownedStatements(
  body: Parser.SyntaxNode,
  types: Set<string>
): Parser.SyntaxNode[] {
  const found: Parser.SyntaxNode[] = [];
  const visit = (node: Parser.SyntaxNode) => {
    for (const child of node.namedChildren) {
      if (types.has(child.type)) found.push(child);
      if (!LOOP_OR_SCOPE.has(child.type)) visit(child);
    }
  };
  visit(body);
  return found;
}

type BindingKind = "mapping" | "sequence" | "unknown";

const MAPPING_FACTORIES = new Set([
  "dict",
  "defaultdict",
  "OrderedDict",
  "Counter",
  "ChainMap",
  "collections.defaultdict",
  "collections.OrderedDict",
  "collections.Counter",
  "collections.ChainMap",
]);
const SEQUENCE_FACTORIES = new Set([
  "list",
  "tuple",
  "range",
  "sorted",
  "reversed",
  "set",
  "frozenset",
]);

function classifyValue(node: Parser.SyntaxNode): BindingKind {
  if (node.type === "dictionary" || node.type === "dictionary_comprehension") {
    return "mapping";
  }
  if (
    node.type === "list" ||
    node.type === "list_comprehension" ||
    node.type === "tuple" ||
    node.type === "set" ||
    node.type === "set_comprehension"
  ) {
    return "sequence";
  }
  if (node.type === "call") {
    const fn = node.childForFieldName("function")?.text ?? "";
    if (MAPPING_FACTORIES.has(fn)) return "mapping";
    if (SEQUENCE_FACTORIES.has(fn)) return "sequence";
  }
  return "unknown";
}

function classifyAnnotation(text: string): BindingKind {
  const head = text.replace(/\s+/g, "").split("[")[0].split(".").pop() ?? "";
  if (/^(dict|Dict|Mapping|MutableMapping|defaultdict|OrderedDict|Counter)$/.test(head)) {
    return "mapping";
  }
  if (/^(list|List|tuple|Tuple|Sequence|set|Set|frozenset|range)$/.test(head)) {
    return "sequence";
  }
  return "unknown";
}

/**
 * Best-effort, file-local type of `name` at `loop`: the last plain binding
 * before the loop in the enclosing scope, or an annotated parameter.
 * Anything else is "unknown" — S4 then reports at Medium confidence.
 */
function bindingKindBefore(
  loop: Parser.SyntaxNode,
  name: string
): BindingKind {
  let scope: Parser.SyntaxNode | null = loop.parent;
  while (
    scope &&
    scope.type !== "function_definition" &&
    scope.type !== "module"
  ) {
    scope = scope.parent;
  }
  if (!scope) return "unknown";

  let kind: BindingKind = "unknown";
  if (scope.type === "function_definition") {
    const params = scope.childForFieldName("parameters");
    for (const p of params ? params.namedChildren : []) {
      if (
        (p.type === "typed_parameter" || p.type === "typed_default_parameter") &&
        (p.namedChildren[0]?.text === name ||
          p.childForFieldName("name")?.text === name)
      ) {
        const ann = p.childForFieldName("type");
        if (ann) kind = classifyAnnotation(ann.text);
      }
    }
  }

  const visit = (node: Parser.SyntaxNode) => {
    for (const child of node.namedChildren) {
      if (child.startIndex >= loop.startIndex) return;
      if (child.type === "function_definition" || child.type === "class_definition") {
        continue;
      }
      if (child.type === "assignment") {
        const left = child.childForFieldName("left");
        if (left?.type === "identifier" && left.text === name) {
          const right = child.childForFieldName("right");
          const ann = child.childForFieldName("type");
          kind = right ? classifyValue(right) : "unknown";
          if (kind === "unknown" && ann) kind = classifyAnnotation(ann.text);
        }
      }
      visit(child);
    }
  };
  const body = scope.type === "module" ? scope : scope.childForFieldName("body");
  if (body) visit(body);
  return kind;
}

function escapeRegExp(text: string): string {
  return text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

/** `X[idx]` subscript read with exactly this value/index pair. */
function isIndexedRead(
  node: Parser.SyntaxNode,
  collectionText: string,
  indexText: string
): boolean {
  if (node.type !== "subscript") return false;
  const value = node.childForFieldName("value");
  const subscript = node.childForFieldName("subscript");
  return (
    value !== null &&
    subscript !== null &&
    value.text === collectionText &&
    subscript.text === indexText
  );
}

/**
 * True when the index variable appears anywhere in `body` outside a plain
 * `X[i]` read (index arithmetic, parallel indexing, slices, call args…).
 * `excludeRanges` skips subtrees by byte offset (e.g. the S2 increment
 * statement); ranges compare by value because tree-sitter wrapper objects
 * do not preserve reference identity across traversals.
 */
function hasNonAccessIndexUse(
  body: Parser.SyntaxNode,
  collectionText: string,
  indexName: string,
  excludeRanges: Array<{ start: number; end: number }> = []
): boolean {
  for (const node of walk(body)) {
    if (node.type !== "identifier" || node.text !== indexName) continue;
    if (
      excludeRanges.some(
        (r) => node.startIndex >= r.start && node.endIndex <= r.end
      )
    ) {
      continue;
    }
    const parent = node.parent;
    if (
      parent &&
      parent.type === "subscript" &&
      parent.childForFieldName("subscript") !== null &&
      parent.childForFieldName("subscript")!.startIndex === node.startIndex &&
      parent.childForFieldName("value")?.text === collectionText
    ) {
      continue; // legitimate `X[i]` element access
    }
    return true;
  }
  return false;
}

/** At least one `X[i]` read anywhere under `body`. */
function hasIndexedRead(
  body: Parser.SyntaxNode,
  collectionText: string,
  indexName: string
): boolean {
  for (const node of walk(body)) {
    if (isIndexedRead(node, collectionText, indexName)) return true;
  }
  return false;
}

/**
 * `total += X[i]`-shaped builtin reduction inside the body.
 * Such loops belong to C10 (bulk primitives), not C3.1.
 */
function hasBuiltinReduction(
  body: Parser.SyntaxNode,
  collectionText: string,
  indexName: string
): boolean {
  for (const node of walk(body)) {
    if (node.type !== "augmented_assignment") continue;
    if (!hasOperator(node, "+=")) continue;
    const right = node.childForFieldName("right");
    if (!right) continue;
    for (const inner of walk(right)) {
      if (isIndexedRead(inner, collectionText, indexName)) return true;
    }
  }
  return false;
}

// ---------------------------------------------------------------------------
// Signal matchers — each returns a LoopMatch or null
// ---------------------------------------------------------------------------

/** S1: `for i in range(len(X))` with an `X[i]` read in the body. */
function matchRangeLen(
  loop: Parser.SyntaxNode,
  body: Parser.SyntaxNode
): LoopMatch | null {
  const left = loop.childForFieldName("left");
  const right = loop.childForFieldName("right");
  if (!left || left.type !== "identifier" || !right) return null;
  if (right.type !== "call") return null;

  const fn = right.childForFieldName("function");
  if (!fn || fn.text !== "range") return null;
  const args = right.childForFieldName("arguments");
  if (!args) return null;
  const rangeArgs = namedChildrenSkippingComments(args);
  // Only the single-argument `range(len(X))` form; `range(n)` counting is idiomatic.
  if (rangeArgs.length !== 1) return null;
  const collectionText = matchLenCall(rangeArgs[0]);
  if (collectionText === null) return null;

  const indexName = left.text;
  if (!hasIndexedRead(body, collectionText, indexName)) return null;
  if (hasNonAccessIndexUse(body, collectionText, indexName)) return null;
  if (mutatesCollection(body, collectionText)) return null;
  if (hasBuiltinReduction(body, collectionText, indexName)) return null;

  return {
    signal: "range-len-indexing",
    symbol: collectionText,
    loopType: "for",
    suggested: `for ${indexName}, <item> in enumerate(${collectionText})`,
    why: `Index-based loop 'for ${indexName} in range(len(${collectionText}))' reads elements via '${collectionText}[${indexName}]'; direct iteration or 'enumerate' expresses the same traversal without repeated indexing.`,
    agentPromptAction: `rewrite the index-based loop to direct iteration ('for <item> in ${collectionText}') or 'enumerate(${collectionText})' when the index itself is needed`,
    confidence: "high",
  };
}

/** S2: `i = 0` … `while i < len(X)` … `i += 1` with an `X[i]` read. */
function matchManualIndexWhile(
  loop: Parser.SyntaxNode,
  body: Parser.SyntaxNode
): LoopMatch | null {
  const condition = loop.childForFieldName("condition");
  if (!condition || condition.type !== "comparison_operator") return null;
  const operands = namedChildrenSkippingComments(condition);
  if (operands.length !== 2) return null;
  const [indexNode, boundNode] = operands;
  if (indexNode.type !== "identifier") return null;
  // Strictly `i < len(X)`; `<=`, `>` and arithmetic bounds are out of scope for v1.
  if (!hasOperator(condition, "<")) return null;
  const collectionText = matchLenCall(boundNode);
  if (collectionText === null) return null;
  const indexName = indexNode.text;

  // Init must be the immediately preceding statement `i = 0` (same block).
  const prev = previousSibling(loop);
  const prevInner = prev ? unwrapExpressionStatement(prev) : null;
  if (!prevInner || prevInner.type !== "assignment") return null;
  const initLeft = prevInner.childForFieldName("left");
  const initRight = prevInner.childForFieldName("right");
  if (
    !initLeft ||
    initLeft.type !== "identifier" ||
    initLeft.text !== indexName ||
    !initRight ||
    initRight.type !== "integer" ||
    initRight.text !== "0"
  ) {
    return null;
  }

  // A linear traversal has no `continue` (it would skip the increment).
  if (ownedStatements(body, new Set(["continue_statement"])).length > 0) {
    return null;
  }

  // Exactly one increment `i += 1` / `i = i + 1`, as a top-level body
  // statement: a conditional or repeated advance is not a plain traversal.
  // Ranges compare by byte offset (see hasNonAccessIndexUse).
  const topLevelRanges = namedChildrenSkippingComments(body).map((s) => ({
    start: s.startIndex,
    end: s.endIndex,
  }));
  const increments: Array<{ start: number; end: number }> = [];
  for (const node of walk(body)) {
    if (
      node.type === "augmented_assignment" &&
      node.childForFieldName("left")?.text === indexName &&
      hasOperator(node, "+=") &&
      node.childForFieldName("right")?.type === "integer" &&
      node.childForFieldName("right")?.text === "1"
    ) {
      increments.push({ start: node.startIndex, end: node.endIndex });
    } else if (
      node.type === "assignment" &&
      node.childForFieldName("left")?.type === "identifier" &&
      node.childForFieldName("left")?.text === indexName
    ) {
      const r = node.childForFieldName("right");
      if (
        r &&
        r.type === "binary_operator" &&
        hasOperator(r, "+") &&
        r.childForFieldName("left")?.text === indexName &&
        r.childForFieldName("right")?.type === "integer" &&
        r.childForFieldName("right")?.text === "1"
      ) {
        increments.push({ start: node.startIndex, end: node.endIndex });
      }
    }
  }
  if (increments.length !== 1) return null;
  const isTopLevel = topLevelRanges.some(
    (r) =>
      r.start <= increments[0].start &&
      r.end >= increments[0].end &&
      // the statement is the increment itself, not a block containing it
      r.end - r.start <= increments[0].end - increments[0].start + 1
  );
  if (!isTopLevel) return null;

  if (!hasIndexedRead(body, collectionText, indexName)) return null;
  if (hasNonAccessIndexUse(body, collectionText, indexName, increments)) {
    return null;
  }
  if (mutatesCollection(body, collectionText)) return null;
  if (hasBuiltinReduction(body, collectionText, indexName)) return null;

  return {
    signal: "manual-index-while",
    symbol: collectionText,
    loopType: "while",
    suggested: `for ${indexName}, <item> in enumerate(${collectionText})`,
    why: `Manual-index 'while ${indexName} < len(${collectionText})' loop with explicit init/increment reads elements via '${collectionText}[${indexName}]'; a 'for' loop over the collection or 'enumerate' expresses the same traversal.`,
    agentPromptAction: `rewrite the manual-index while loop to a 'for' loop over ${collectionText} (or 'enumerate(${collectionText})' when the index itself is needed)`,
    confidence: "high",
  };
}

/** `out.append(E)` single-argument call on the accumulator, else null. */
function matchAccumulatorAppend(
  statement: Parser.SyntaxNode,
  accumulator: string
): boolean {
  const inner = unwrapExpressionStatement(statement);
  if (!inner || inner.type !== "call") return false;
  const fn = inner.childForFieldName("function");
  if (!fn || fn.type !== "attribute") return false;
  const obj = fn.childForFieldName("object");
  const attrName = fn.namedChildren[fn.namedChildren.length - 1];
  if (!obj || obj.type !== "identifier" || obj.text !== accumulator) {
    return false;
  }
  if (!attrName || attrName.text !== "append") return false;
  const args = inner.childForFieldName("arguments");
  if (!args) return false;
  return namedChildrenSkippingComments(args).length === 1;
}

/**
 * S3: `out = []` immediately before the loop plus a single (optionally
 * `if`-gated, no `else`) `out.append(...)` statement.
 * Takes precedence over S1/S4 on the same loop: the comprehension rewrite
 * subsumes the header-form question.
 */
function matchAppendAccumulation(
  loop: Parser.SyntaxNode,
  body: Parser.SyntaxNode
): LoopMatch | null {
  const prev = previousSibling(loop);
  const prevInner = prev ? unwrapExpressionStatement(prev) : null;
  if (!prevInner || prevInner.type !== "assignment") return null;
  const accLeft = prevInner.childForFieldName("left");
  const accRight = prevInner.childForFieldName("right");
  if (
    !accLeft ||
    accLeft.type !== "identifier" ||
    !accRight ||
    accRight.type !== "list" ||
    namedChildrenSkippingComments(accRight).length !== 0
  ) {
    return null;
  }
  const accumulator = accLeft.text;

  const loopLeft = loop.childForFieldName("left");
  const loopRight = loop.childForFieldName("right");
  if (!loopLeft || loopLeft.type !== "identifier" || !loopRight) return null;

  const statements = namedChildrenSkippingComments(body);
  if (statements.length !== 1) return null;

  let gated = false;
  let appendRow: number;
  const only = statements[0];
  if (only.type === "if_statement") {
    if (only.childForFieldName("alternative") !== null) return null;
    const consequence = only.childForFieldName("consequence");
    if (!consequence) return null;
    const inner = namedChildrenSkippingComments(consequence);
    if (inner.length !== 1 || !matchAccumulatorAppend(inner[0], accumulator)) {
      return null;
    }
    gated = true;
    appendRow = inner[0].startPosition.row;
  } else if (!matchAccumulatorAppend(only, accumulator)) {
    return null;
  } else {
    appendRow = only.startPosition.row;
  }

  // Mechanical comprehension blockers anywhere in the body (incl. await in E).
  for (const node of walk(body)) {
    if (COMPREHENSION_BLOCKERS.has(node.type)) return null;
  }

  // The accumulator must not be read or re-bound inside the loop.
  for (const node of walk(body)) {
    if (node.type !== "identifier" || node.text !== accumulator) continue;
    const parent = node.parent;
    const receiver = parent?.childForFieldName("object") ?? null;
    if (
      parent &&
      parent.type === "attribute" &&
      receiver !== null &&
      receiver.startIndex === node.startIndex &&
      receiver.endIndex === node.endIndex
    ) {
      continue; // the `out.append` receiver itself
    }
    return null;
  }

  // Mutating the iterated collection belongs to C3.7. Self-append (`out`
  // is also the iterable) is both a mutation and a likely infinite loop.
  const iterableText = loopRight.text;
  const iterableRoot =
    loopRight.type === "identifier" ? loopRight.text : null;
  if (accumulator === iterableText || accumulator === iterableRoot) {
    return null;
  }
  if (iterableRoot !== null && mutatesCollection(body, iterableRoot)) {
    return null;
  }

  const iterVar = loopLeft.text;
  const signal: Signal = gated
    ? "append-accumulation-gated"
    : "append-accumulation";
  const forKw = isAsyncFor(loop) ? "async for" : "for";
  const suggested = gated
    ? `[<expr> ${forKw} ${iterVar} in ${iterableText} if <cond>]`
    : `[<expr> ${forKw} ${iterVar} in ${iterableText}]`;
  return {
    signal,
    symbol: accumulator,
    loopType: "for",
    suggested,
    why: `Loop accumulates results with a single${gated ? " (if-gated)" : ""} '${accumulator}.append(...)' call; a list comprehension (or a bulk builtin where one applies — see C10) expresses the same construction without per-iteration method-call overhead.`,
    agentPromptAction: `rewrite the append-accumulation loop to the comprehension '${suggested}' (preferring direct iteration / enumerate inside it); if the loop reduces to a builtin (sum, join, …) prefer that per C10 instead`,
    confidence: "high",
    suppressionRows: [appendRow],
  };
}

/** S4: `for k in D` / `for k in D.keys()` with a `D[k]` read in the body. */
function matchDictKeyLookup(
  loop: Parser.SyntaxNode,
  body: Parser.SyntaxNode
): LoopMatch | null {
  const left = loop.childForFieldName("left");
  const right = loop.childForFieldName("right");
  if (!left || left.type !== "identifier" || !right) return null;
  const keyName = left.text;

  let dictText: string | null = null;
  // `.keys()` proves a mapping; a bare name may be a list of indices
  // (`for i in perm: perm[i]`), where `.items()` would be wrong advice.
  let confidence: Confidence = "high";
  const extraLimitations: string[] = [];
  if (right.type === "identifier") {
    dictText = right.text;
    const kind = bindingKindBefore(loop, dictText);
    if (kind === "sequence") return null;
    if (kind === "unknown") {
      confidence = "medium";
      extraLimitations.push(
        `'${dictText}' is not provably a dict in this file; '.items()' applies only if it is a mapping (a list iterated by its own values as indices is not).`
      );
    }
  } else if (right.type === "call") {
    const fn = right.childForFieldName("function");
    if (!fn || fn.type !== "attribute") return null;
    const obj = fn.childForFieldName("object");
    const attrName = fn.namedChildren[fn.namedChildren.length - 1];
    if (!obj || !attrName || attrName.text !== "keys") return null;
    const args = right.childForFieldName("arguments");
    if (
      !args ||
      namedChildrenSkippingComments(args).length !== 0
    ) {
      return null;
    }
    dictText = obj.text;
  } else {
    return null;
  }

  if (!hasIndexedRead(body, dictText, keyName)) return null;
  // Assignments like `d[k] = v`, `del d[k]` or `d.pop(k)` belong to C3.7.
  if (mutatesCollection(body, dictText)) return null;
  // `total += d[k]` belongs to C10 (bulk primitives).
  if (hasBuiltinReduction(body, dictText, keyName)) return null;

  return {
    signal: "dict-key-lookup",
    symbol: dictText,
    loopType: "for",
    suggested: `for ${keyName}, <value> in ${dictText}.items()`,
    why: `Key loop 'for ${keyName} in ${dictText}' re-reads each value via '${dictText}[${keyName}]'; iterating '${dictText}.items()' yields both directly without the repeated lookup.`,
    agentPromptAction: `rewrite the key loop to 'for ${keyName}, <value> in ${dictText}.items()'${confidence === "high" ? "" : ` after confirming '${dictText}' is a dict`}`,
    confidence,
    extraLimitations,
  };
}

// ---------------------------------------------------------------------------
// Finding construction + entry point
// ---------------------------------------------------------------------------

function buildFinding(
  match: LoopMatch,
  signal: Signal,
  loop: Parser.SyntaxNode,
  filePath: string,
  sourceLines: string[],
  ordinal: number
): Finding | null {
  const startLine = loop.startPosition.row + 1;
  const endLine = loop.endPosition.row + 1;
  const headerLine = (sourceLines[loop.startPosition.row] ?? "").trim();

  const suppressed = [loop.startPosition.row, ...(match.suppressionRows ?? [])].some(
    (row) => isLineSuppressed(sourceLines[row] ?? "", [CHECK]).isSuppressed
  );
  if (suppressed) return null;

  const normalizedHeader = headerLine.replace(/\s+/g, " ");
  const scope = enclosingScopeName(loop);
  const identity = `${KIND}:${scope}:${signal}:${normalizedHeader}:${ordinal}`;
  const fingerprint = generateFingerprint(
    CHECK,
    KIND,
    filePath,
    `${scope}:${signal}:${normalizedHeader}:${ordinal}`
  );

  const location = { path: filePath, startLine, endLine };
  return {
    check: CHECK,
    kind: KIND,
    fingerprint,
    identity,
    location,
    evidence: {
      snippet: headerLine,
      symbol: match.symbol,
      loopType: match.loopType,
      suggested: match.suggested,
    },
    why: match.why,
    severity: "low",
    confidence: match.confidence,
    limitations: [...STATIC_LIMITATIONS, ...(match.extraLimitations ?? [])],
    evidenceTier: "static",
    impact: {
      quantified: false,
      reason: STATIC_IMPACT_REASON,
    },
    references: [{ ...SRC01_REFERENCE }],
    agentPrompt: `In ${filePath}:${startLine}-${endLine}, ${match.agentPromptAction} (line: "${headerLine}"). Static finding only — profile before treating it as a must-fix; keep the change minimal and covered by tests.`,
    detector: {
      id: "owner-a-static-scan",
      version: DETECTOR_VERSION,
    },
  };
}

/**
 * Pure function detecting inefficient iteration constructs (static half).
 * Never performs I/O or executes code; returns [] on syntax errors.
 */
export function detectInefficientIteration(
  rootNode: Parser.SyntaxNode,
  filePath: string,
  sourceLines: string[]
): Finding[] {
  const findings: Finding[] = [];
  const ordinalByKey = new Map<string, number>();

  const loops: Parser.SyntaxNode[] = [];
  for (const node of walk(rootNode)) {
    if (node.type === "for_statement" || node.type === "while_statement") {
      loops.push(node);
    }
  }

  for (const loop of loops) {
    const body = loop.childForFieldName("body");
    if (!body) continue;

    let match: LoopMatch | null = null;
    let signal: Signal | null = null;
    if (loop.type === "for_statement") {
      // S3 first: the comprehension rewrite subsumes the header-form question.
      const s3 = matchAppendAccumulation(loop, body);
      if (s3) {
        match = s3;
        signal =
          s3.signal === "append-accumulation-gated"
            ? "append-accumulation-gated"
            : "append-accumulation";
      } else {
        const s1 = matchRangeLen(loop, body);
        if (s1) {
          match = s1;
          signal = "range-len-indexing";
        } else {
          const s4 = matchDictKeyLookup(loop, body);
          if (s4) {
            match = s4;
            signal = "dict-key-lookup";
          }
        }
      }
    } else {
      const s2 = matchManualIndexWhile(loop, body);
      if (s2) {
        match = s2;
        signal = "manual-index-while";
      }
    }

    if (!match || !signal) continue;

    const scope = enclosingScopeName(loop);
    const headerLine = (sourceLines[loop.startPosition.row] ?? "")
      .trim()
      .replace(/\s+/g, " ");
    const key = `${scope}::${signal}::${headerLine}`;
    const ordinal = ordinalByKey.get(key) ?? 0;
    ordinalByKey.set(key, ordinal + 1);

    const finding = buildFinding(
      match,
      signal,
      loop,
      filePath,
      sourceLines,
      ordinal
    );
    if (finding) findings.push(finding);
  }

  return findings.sort((a, b) => a.location.startLine - b.location.startLine);
}
