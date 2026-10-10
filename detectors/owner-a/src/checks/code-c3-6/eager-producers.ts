import Parser from "tree-sitter";
import { Confidence, Finding, Severity, generateFingerprint } from "../../core/finding.js";
import { isLineSuppressed } from "../../core/suppressions.js";
import {
  collectAllIdentifiers,
  getEnclosingQualname,
  getRootIdentifier,
  isVolatileCall,
  loopExitsEarly,
} from "../../core/loops.js";

const CHECK = "CODE-C3.6";
const DETECTOR_VERSION = "0.1.0";

const REFERENCES = [
  {
    id: "SRC-01",
    title:
      "Watts This Smell: A Comprehensive Taxonomy of Software Energy Smells",
    url: "https://arxiv.org/abs/2604.04809",
  },
  {
    id: "taxonomy-c3.6",
    title: "Software Compute Waste Taxonomy — C3.6 Unfiltered bulk iteration",
    url: "https://github.com/AWS-env/environmental-hacks/issues/65",
  },
];

const CONDITIONAL_PAYOFF =
  "Static only: the payoff is the share of elements the consumer never reads (k / n, or the position of the first match), which is not visible statically.";
const SKIPPED_SIDE_EFFECTS =
  "A lazy producer runs the per-element calls only for consumed elements; verify they have no required side effects (validation, caching, logging).";
const SINGLE_USE =
  "A generator can be iterated only once; keep the rewrite local to this single consumer.";

type Kind = "eager-then-prefix" | "eager-then-early-exit" | "eager-then-short-circuit";
type ProducerType = "comprehension" | "wrapped-lazy" | "file-read";

interface Producer {
  node: Parser.SyntaxNode;
  type: ProducerType;
  /** Lazy equivalent of the producer (generator / iterator text), for the prompt. */
  lazyText: string;
  /** Subtrees evaluated once per element (element expression, `if` clauses, mapped callee). */
  perElement: Parser.SyntaxNode[];
  /** Iterables the producer is built from. */
  sources: Parser.SyntaxNode[];
}

interface Consumer {
  kind: Kind;
  node: Parser.SyntaxNode;
  /** Short description used in `why`. */
  shape: string;
  confidence: Confidence;
  /** Prefix length for `[:k]` consumers. */
  stop?: string;
}

const LAZY_BUILTINS = new Set(["map", "filter", "zip", "enumerate", "reversed"]);
const MUTATING_METHODS = new Set([
  "append",
  "extend",
  "insert",
  "pop",
  "popleft",
  "remove",
  "clear",
  "add",
  "discard",
  "update",
  "setdefault",
  "write",
  "writelines",
  "send",
  "sendall",
  "put",
  "put_nowait",
  "log",
  "debug",
  "info",
  "warning",
  "warn",
  "error",
  "critical",
  "exception",
]);
const SMALL_LITERAL_MAX = 8;

const ws = (text: string) => text.replace(/\s+/g, " ").trim();

function unwrapParens(node: Parser.SyntaxNode): Parser.SyntaxNode {
  let curr = node;
  while (curr.parent?.type === "parenthesized_expression") curr = curr.parent;
  return curr;
}

function calleeName(call: Parser.SyntaxNode): string | null {
  const fn = call.childForFieldName("function");
  return fn ? fn.text : null;
}

/** Single positional argument of a call (or the bare generator of `f(x for x in xs)`). */
function soleArgument(call: Parser.SyntaxNode): Parser.SyntaxNode | null {
  const args = call.childForFieldName("arguments");
  if (!args) return null;
  if (args.type === "generator_expression") return args;
  const named = args.namedChildren.filter((c) => c.type !== "comment");
  return named.length === 1 ? named[0] : null;
}

function isLazyBuiltinCall(node: Parser.SyntaxNode): boolean {
  return node.type === "call" && LAZY_BUILTINS.has(calleeName(node) ?? "");
}

function comprehensionParts(node: Parser.SyntaxNode): Pick<Producer, "perElement" | "sources"> {
  const perElement: Parser.SyntaxNode[] = [];
  const sources: Parser.SyntaxNode[] = [];
  const body = node.childForFieldName("body");
  if (body) perElement.push(body);
  for (const clause of node.namedChildren) {
    if (clause.type === "for_in_clause") {
      const right = clause.childForFieldName("right");
      if (right) sources.push(right);
    } else if (clause.type === "if_clause") {
      perElement.push(clause);
    }
  }
  return { perElement, sources };
}

function lazyCallParts(call: Parser.SyntaxNode): Pick<Producer, "perElement" | "sources"> {
  const args = call.childForFieldName("arguments");
  const named = args ? args.namedChildren.filter((c) => c.type !== "comment") : [];
  const name = calleeName(call);
  // map(f, xs) / filter(p, xs): the first argument is applied per element.
  if (name === "map" || name === "filter") {
    return { perElement: named.slice(0, 1), sources: named.slice(1) };
  }
  return { perElement: [], sources: named };
}

/** Match an eager producer node, or null. */
function matchProducer(node: Parser.SyntaxNode): Producer | null {
  if (node.type === "list_comprehension") {
    const inner = node.text.slice(1, -1);
    return { node, type: "comprehension", lazyText: `(${inner})`, ...comprehensionParts(node) };
  }

  if (node.type === "call") {
    const name = calleeName(node);
    const fn = node.childForFieldName("function");

    // list(<gen>) / tuple(<gen>) / list(map(...)) ...
    if (name === "list" || name === "tuple") {
      const arg = soleArgument(node);
      if (arg?.type === "generator_expression") {
        return { node, type: "comprehension", lazyText: arg.text, ...comprehensionParts(arg) };
      }
      if (arg && isLazyBuiltinCall(arg)) {
        return { node, type: "wrapped-lazy", lazyText: arg.text, ...lazyCallParts(arg) };
      }
      return null;
    }

    // f.readlines() / f.read().splitlines() / f.read().split(...)
    if (fn?.type === "attribute") {
      const method = fn.childForFieldName("attribute")?.text;
      const obj = fn.childForFieldName("object");
      if (!obj) return null;
      const noArgs = (node.childForFieldName("arguments")?.namedChildCount ?? 1) === 0;
      if (method === "readlines" && noArgs) {
        return { node, type: "file-read", lazyText: obj.text, perElement: [], sources: [obj] };
      }
      if ((method === "splitlines" || method === "split") && obj.type === "call") {
        const readFn = obj.childForFieldName("function");
        const readNoArgs = (obj.childForFieldName("arguments")?.namedChildCount ?? 1) === 0;
        const file = readFn?.childForFieldName("object");
        if (readFn?.type === "attribute" && readFn.childForFieldName("attribute")?.text === "read" && readNoArgs && file) {
          // split() with no separator splits on all whitespace — not a line read.
          if (method === "split" && noArgs) return null;
          return { node, type: "file-read", lazyText: file.text, perElement: [], sources: [file] };
        }
      }
    }
    return null;
  }

  // [*map(f, xs)] — tree-sitter parses the splat-call as call(function: list_splat).
  if (node.type === "list") {
    const items = node.namedChildren.filter((c) => c.type !== "comment");
    if (items.length !== 1) return null;
    const item = items[0];
    let lazy: Parser.SyntaxNode | null = null;
    if (item.type === "list_splat" && item.namedChildren[0] && isLazyBuiltinCall(item.namedChildren[0])) {
      lazy = item.namedChildren[0];
    } else if (item.type === "call" && item.childForFieldName("function")?.type === "list_splat") {
      const inner = item.childForFieldName("function")!.namedChildren[0];
      if (inner?.type === "identifier" && LAZY_BUILTINS.has(inner.text)) lazy = item;
    }
    if (!lazy) return null;
    const lazyText = lazy.text.replace(/^\*\s*/, "");
    const name = lazyText.split("(")[0].trim();
    const args = lazy.childForFieldName("arguments");
    const named = args ? args.namedChildren.filter((c) => c.type !== "comment") : [];
    const parts =
      name === "map" || name === "filter"
        ? { perElement: named.slice(0, 1), sources: named.slice(1) }
        : { perElement: [], sources: named };
    return { node, type: "wrapped-lazy", lazyText, ...parts };
  }

  return null;
}

function isZeroInteger(node: Parser.SyntaxNode | null): boolean {
  return node?.type === "integer" && Number(node.text) === 0;
}

/** `[:k]` / `[0:k]` with a non-negative stop and no step. */
function isPrefixSlice(slice: Parser.SyntaxNode): boolean {
  // Children: [start] ':' [stop] [':' [step]]
  const parts: (Parser.SyntaxNode | null)[] = [null];
  for (const child of slice.children) {
    if (child.type === ":") parts.push(null);
    else parts[parts.length - 1] = child;
  }
  if (parts.length !== 2) return false; // has a step
  const [start, stop] = parts;
  if (start && !isZeroInteger(start)) return false;
  if (!stop) return false; // `[:]` is a full copy
  if (stop.type === "unary_operator" && stop.text.trim().startsWith("-")) return false;
  return stop.type === "integer" || stop.type === "identifier" || stop.type === "attribute";
}

/** Classify the node standing in for P (the producer itself, or its single read). */
function classifyConsumer(p: Parser.SyntaxNode): Consumer | null {
  const at = unwrapParens(p);
  const parent = at.parent;
  if (!parent) return null;

  // S1: P[0] / P[:k]
  if (parent.type === "subscript" && parent.childForFieldName("value")?.id === at.id) {
    const index = parent.childForFieldName("subscript");
    const subscripts = parent.namedChildren.filter((c) => c.id !== at.id);
    if (subscripts.length !== 1 || !index) return null;
    if (isZeroInteger(index)) {
      return { kind: "eager-then-prefix", node: parent, shape: "uses only element [0]", confidence: "high" };
    }
    if (index.type === "slice" && isPrefixSlice(index)) {
      const stop = index.text.slice(index.text.indexOf(":") + 1).trim();
      return { kind: "eager-then-prefix", node: parent, shape: `uses only the prefix [${index.text}]`, confidence: "high", stop };
    }
    return null;
  }

  if (parent.type === "argument_list") {
    const call = parent.parent;
    const args = parent.namedChildren.filter((c) => c.type !== "comment");
    if (!call || call.type !== "call" || args[0]?.id !== at.id) return null;
    const name = calleeName(call);

    // S1: next(iter(P)) / next(iter(P), default)
    if (name === "iter" && args.length === 1) {
      const outerList = unwrapParens(call).parent;
      const outer = outerList?.parent;
      if (outerList?.type === "argument_list" && outer?.type === "call" && calleeName(outer) === "next" && outerList.namedChildren[0]?.id === unwrapParens(call).id) {
        return { kind: "eager-then-prefix", node: outer, shape: "uses only the first element via next(iter(...))", confidence: "high" };
      }
      return null;
    }
    // S3: any(P) / all(P)
    if ((name === "any" || name === "all") && args.length === 1) {
      return { kind: "eager-then-short-circuit", node: call, shape: `is consumed by ${name}(), which stops at the first decisive element`, confidence: "high" };
    }
    return null;
  }

  // S3: v in P / v not in P
  if (parent.type === "comparison_operator") {
    const operands = parent.namedChildren.filter((c) => c.type !== "comment");
    if (operands.length !== 2 || operands[1].id !== at.id) return null;
    const op = parent.children
      .filter((c) => !c.isNamed)
      .map((c) => c.text)
      .join(" ");
    if (op !== "in" && op !== "not in") return null;
    return { kind: "eager-then-short-circuit", node: parent, shape: `is only tested with '${op}', which stops at the first match`, confidence: "high" };
  }

  // S2: for ... in P: with an exit owned by that loop
  if (parent.type === "for_statement" && parent.childForFieldName("right")?.id === at.id) {
    if (!loopExitsEarly(parent)) return null;
    return { kind: "eager-then-early-exit", node: parent, shape: "is iterated by a loop that can stop early (break/return/raise)", confidence: "medium" };
  }

  return null;
}

function functionScope(node: Parser.SyntaxNode): Parser.SyntaxNode | null {
  let curr = node.parent;
  while (curr) {
    if (curr.type === "function_definition") return curr;
    if (curr.type === "class_definition" || curr.type === "module" || curr.type === "lambda") return null;
    curr = curr.parent;
  }
  return null;
}

const REPEATING = new Set([
  "for_statement",
  "while_statement",
  "list_comprehension",
  "set_comprehension",
  "dictionary_comprehension",
  "generator_expression",
]);

function isAncestor(a: Parser.SyntaxNode, b: Parser.SyntaxNode): boolean {
  for (let curr = b.parent; curr; curr = curr.parent) if (curr.id === a.id) return true;
  return false;
}

/**
 * One hop: `name = P` as a plain statement inside a function, where `name`
 * occurs exactly twice in that function (this binding and one later read, not
 * inside a loop the binding is outside of). Returns the read, or null.
 */
function singleUseRead(producer: Parser.SyntaxNode): { name: string; read: Parser.SyntaxNode } | null {
  const at = unwrapParens(producer);
  const assign = at.parent;
  if (assign?.type !== "assignment" || assign.childForFieldName("right")?.id !== at.id) return null;
  if (assign.parent?.type !== "expression_statement") return null;
  const left = assign.childForFieldName("left");
  if (left?.type !== "identifier") return null;
  const fn = functionScope(assign);
  if (!fn) return null;

  const name = left.text;
  const occurrences: Parser.SyntaxNode[] = [];
  const visit = (n: Parser.SyntaxNode) => {
    if (n.type === "identifier" && n.text === name) occurrences.push(n);
    for (const child of n.namedChildren) visit(child);
  };
  visit(fn);
  if (occurrences.length !== 2) return null;
  const read = occurrences.find((o) => o.id !== left.id);
  if (!read || read.startIndex < assign.endIndex) return null;

  for (let curr = read.parent; curr && curr.id !== fn.id; curr = curr.parent) {
    if (REPEATING.has(curr.type) && !isAncestor(curr, assign)) {
      // The read itself may be the iterable of that loop (`for r in rows:`).
      if (curr.type === "for_statement" && curr.childForFieldName("right")?.id === unwrapParens(read).id) continue;
      return null;
    }
  }
  return { name, read };
}

function hasSideEffectCall(node: Parser.SyntaxNode): boolean {
  if (node.type === "call") {
    const fn = node.childForFieldName("function");
    if (fn && isVolatileCall(fn.text)) return true;
    if (fn?.type === "attribute") {
      const method = fn.childForFieldName("attribute")?.text ?? "";
      if (MUTATING_METHODS.has(method)) return true;
    }
  }
  return node.namedChildren.some(hasSideEffectCall);
}

function containsCall(node: Parser.SyntaxNode): boolean {
  return node.type === "call" || node.namedChildren.some(containsCall);
}

/** Per-element work is observable: volatile/mutating calls, or a volatile mapped callee. */
function perElementHasSideEffects(producer: Producer): boolean {
  return producer.perElement.some((n) =>
    producer.type === "wrapped-lazy" && (n.type === "identifier" || n.type === "attribute")
      ? isVolatileCall(n.text) || MUTATING_METHODS.has(n.text.split(".").pop() ?? "")
      : hasSideEffectCall(n)
  );
}

function isSmallLiteral(source: Parser.SyntaxNode): boolean {
  if (source.type === "tuple" || source.type === "list" || source.type === "set") {
    return source.namedChildren.filter((c) => c.type !== "comment").length <= SMALL_LITERAL_MAX;
  }
  if (source.type === "call" && calleeName(source) === "range") {
    const args = source.childForFieldName("arguments")?.namedChildren ?? [];
    const stop = args.length === 1 ? args[0] : args.length === 2 ? args[1] : null;
    const start = args.length === 2 ? args[0] : null;
    if (stop?.type !== "integer") return false;
    const lo = start?.type === "integer" ? Number(start.text) : start ? NaN : 0;
    return Number(stop.text) - lo <= SMALL_LITERAL_MAX;
  }
  return false;
}

/** S2: the loop body mutates a source the producer was built from (copy-to-mutate, C3.7). */
function loopMutatesSource(loop: Parser.SyntaxNode, producer: Producer): boolean {
  const roots = new Set(producer.sources.map((s) => getRootIdentifier(s)).filter((r): r is string => !!r));
  if (roots.size === 0) return false;
  const body = loop.childForFieldName("body");
  if (!body) return false;
  const touches = (n: Parser.SyntaxNode): boolean => {
    if (n.type === "call") {
      const fn = n.childForFieldName("function");
      if (fn?.type === "attribute" && MUTATING_METHODS.has(fn.childForFieldName("attribute")?.text ?? "")) {
        const root = getRootIdentifier(fn.childForFieldName("object"));
        if (root && roots.has(root)) return true;
      }
    }
    if (n.type === "delete_statement") {
      if (collectAllIdentifiers(n).some((id) => roots.has(id))) return true;
    }
    if (n.type === "assignment" || n.type === "augmented_assignment") {
      const left = n.childForFieldName("left");
      if (left && (left.type === "subscript" || left.type === "attribute")) {
        const root = getRootIdentifier(left);
        if (root && roots.has(root)) return true;
      }
    }
    return n.namedChildren.some(touches);
  };
  return touches(body);
}

/** `fn(<lazy>)`, without doubling the parentheses of a generator expression. */
function callOn(fn: string, lazy: string, extra = ""): string {
  const arg = lazy.startsWith("(") && lazy.endsWith(")") && !extra ? lazy.slice(1, -1) : lazy;
  return `${fn}(${arg}${extra})`;
}

function promptFor(kind: Kind, producer: Producer, consumer: Consumer, symbol: string | null): string {
  const lazy = producer.lazyText;
  const target = symbol ? `'${symbol}'` : "the producer";
  if (producer.type === "file-read") {
    if (kind === "eager-then-early-exit") {
      return `iterate the file object directly (\`for line in ${lazy}:\`) instead of reading every line up front; lines then keep their trailing newline, so strip it where the old code relied on splitting (and keep the read inside a \`with\` block so the handle is closed)`;
    }
    if (kind === "eager-then-prefix") {
      return `read only the lines needed (\`${lazy}.readline()\` for the first, or \`list(itertools.islice(${lazy}, k))\` for a prefix) instead of reading the whole file`;
    }
    return `test the lines lazily by iterating the file object (\`any(... for line in ${lazy})\`) instead of reading every line up front`;
  }
  switch (kind) {
    case "eager-then-prefix":
      if (consumer.shape.includes("[0]") || consumer.shape.includes("next(iter")) {
        return `replace ${target} with \`${callOn("next", lazy)}\` (add a default, \`next(..., None)\`, if an empty input must not raise) so only the first element is computed`;
      }
      return `replace ${target} with \`list(${callOn("itertools.islice", lazy, `, ${consumer.stop ?? "k"}`)})\` so only the first ${consumer.stop ?? "k"} elements are computed`;
    case "eager-then-early-exit":
      return `iterate the lazy form \`${lazy}\` instead of building the full list first, so elements after the loop exits are never computed`;
    case "eager-then-short-circuit":
      return `pass the lazy form \`${lazy}\` instead of the list (drop the brackets / list() call) so evaluation stops at the first decisive element`;
  }
}

/**
 * Pure function detecting eager producers whose only consumer uses a subset of
 * the elements (CODE-C3.6). Never performs I/O or executes code.
 */
export function detectEagerProducers(
  rootNode: Parser.SyntaxNode,
  filePath: string,
  sourceLines: string[]
): Finding[] {
  const findings: Finding[] = [];
  const ordinals = new Map<string, number>();

  const visit = (node: Parser.SyntaxNode) => {
    const producer = matchProducer(node);
    if (producer) {
      const finding = evaluate(producer);
      if (finding) {
        findings.push(finding);
        return; // report only the outermost producer
      }
    }
    for (const child of node.namedChildren) visit(child);
  };

  const evaluate = (producer: Producer): Finding | null => {
    const hop = singleUseRead(producer.node);
    const consumer = classifyConsumer(hop ? hop.read : producer.node);
    if (!consumer) return null;

    if (perElementHasSideEffects(producer)) return null;
    if (producer.sources.some(isSmallLiteral)) return null;
    if (consumer.kind === "eager-then-early-exit" && loopMutatesSource(consumer.node, producer)) return null;

    const startRow = producer.node.startPosition.row;
    const endRow = producer.node.endPosition.row;
    const consumerRow = consumer.node.startPosition.row;
    const rows = new Set([startRow, endRow, consumerRow]);
    if ([...rows].some((r) => isLineSuppressed(sourceLines[r] ?? "", [CHECK]).isSuppressed)) return null;

    const perElementCall =
      producer.type !== "comprehension" || producer.perElement.some(containsCall);
    const severity: Severity =
      producer.type === "file-read" || (producer.perElement.length > 0 && perElementCall) ? "medium" : "low";

    const symbol = hop?.name ?? null;
    const qualname = getEnclosingQualname(producer.node);
    const anchor = `${qualname}:${consumer.kind}:${ws(producer.node.text)}`;
    const ordinal = ordinals.get(anchor) ?? 0;
    ordinals.set(anchor, ordinal + 1);
    const id = ordinal === 0 ? anchor : `${anchor}:${ordinal}`;

    const producerLine = (sourceLines[startRow] ?? "").trim();
    const consumerLine = (sourceLines[consumerRow] ?? "").trim();
    const snippet = consumerRow === startRow ? producerLine : `${producerLine}\n${consumerLine}`;

    const limitations = [CONDITIONAL_PAYOFF];
    if (producer.perElement.some(containsCall) || producer.type === "wrapped-lazy") {
      limitations.push(SKIPPED_SIDE_EFFECTS);
    }
    if (hop) limitations.push(SINGLE_USE);
    if (severity === "low") {
      limitations.push("Trivial projection: only the allocation of unconsumed elements is saved.");
    }

    const subject = hop ? `'${hop.name}' (${ws(producer.node.text)})` : `'${ws(producer.node.text)}'`;
    const why = `${subject} builds every element eagerly, but its only consumer ${consumer.shape}; a lazy producer would do the per-element work only for the elements actually consumed.`;

    return {
      check: CHECK,
      kind: consumer.kind,
      fingerprint: generateFingerprint(CHECK, consumer.kind, filePath, id),
      identity: id,
      location: { path: filePath, startLine: startRow + 1, endLine: endRow + 1 },
      evidence: symbol ? { snippet, symbol } : { snippet },
      why,
      severity,
      confidence: consumer.confidence,
      limitations,
      evidenceTier: "static",
      impact: {
        quantified: false,
        reason:
          "Saved work = unconsumed elements × per-element cost; neither is measured statically.",
      },
      references: REFERENCES.map((r) => ({ ...r })),
      agentPrompt: `In ${filePath}:${startRow + 1}${consumerRow !== startRow ? ` (consumed at line ${consumerRow + 1})` : ""}, ${promptFor(consumer.kind, producer, consumer, symbol)}. Static finding only — keep the change minimal and covered by tests.`,
      detector: { id: "owner-a-static-scan", version: DETECTOR_VERSION },
    };
  };

  visit(rootNode);
  return findings.sort((a, b) => a.location.startLine - b.location.startLine);
}
