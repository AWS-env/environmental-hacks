import Parser from "tree-sitter";
import { Confidence, Finding, generateFingerprint } from "../../core/finding.js";
import { isLineSuppressed } from "../../core/suppressions.js";
import { LoopType, checkRunsAtMostOnce, getEnclosingQualname, isNameChain } from "../../core/loops.js";

const CHECK = "CODE-C5.1";
const KIND = "list-membership-in-loop";
const DETECTOR_VERSION = "0.1.0";

/** A list literal with at most this many elements is not flagged (too small to matter). */
export const SMALL_LITERAL_MAX = 8;

const REFERENCES = [
  {
    id: "SRC-01",
    title: "Watts This Smell: A Comprehensive Taxonomy of Software Energy Smells",
    url: "https://arxiv.org/abs/2604.04809",
  },
  {
    id: "taxonomy-c5.1",
    title: "Software Compute Waste Taxonomy - C5.1 Inefficient structure choice",
    url: "https://github.com/AWS-env/environmental-hacks/issues/75",
  },
];

const LIMITATIONS = [
  "Binding resolved only inside this file; list size and trip count are not measured, so the cost of the repeated linear scans is not quantified.",
  "A set requires hashable elements, and equality-vs-hash semantics must match: elements of an unhashable type or with a custom __eq__ cannot safely go in a set.",
];

const COMPREHENSIONS = new Set(["list_comprehension", "set_comprehension", "dictionary_comprehension", "generator_expression"]);
const LOOPS = new Set(["for_statement", "while_statement"]);
const BOUNDARIES = new Set(["function_definition", "class_definition", "lambda", "module"]);
const MUTATING_METHODS = new Set(["append", "extend", "insert", "remove", "pop", "clear", "update", "setdefault", "add", "popitem"]);
const LIST_ANNOTATION = /^(?:typing\.)?(?:list|List|tuple|Tuple|Sequence)\b/;

const ws = (text: string) => text.replace(/\s+/g, "");

/** What one binding of the name looks like. `other` means not a list/tuple (or unknown). */
type Bound =
  | { kind: "other"; /** element/entry count when bound to a set or dict literal */ lit?: number | null; dict?: boolean; derivedFrom?: string | null }
  | { kind: "list"; /** known element count, or null when not statically known */ size: number | null; strong: boolean; derivedFrom?: string | null };

/**
 * For a single-clause comprehension/generator, the name chain its only iterable reads
 * (`x`, `x.items()`, `x.keys()`, `x.values()`); null otherwise. It can only filter or map that
 * collection, so its size never exceeds the source's.
 */
function derivedFromOf(comp: Parser.SyntaxNode): string | null {
  const clauses = comp.namedChildren.filter((c) => c.type === "for_in_clause");
  if (clauses.length !== 1) return null;
  let it = clauses[0].childForFieldName("right");
  while (it?.type === "parenthesized_expression" && it.namedChildren[0]) it = it.namedChildren[0];
  if (!it) return null;
  if (isNameChain(it)) return ws(it.text);
  if (it.type === "call") {
    const fn = it.childForFieldName("function");
    const args = nonComment(it.childForFieldName("arguments")?.namedChildren ?? []);
    const obj = fn?.type === "attribute" ? fn.childForFieldName("object") : null;
    const method = fn?.type === "attribute" ? fn.childForFieldName("attribute")?.text : "";
    if (args.length === 0 && obj && isNameChain(obj) && (method === "items" || method === "keys" || method === "values")) {
      return ws(obj.text);
    }
  }
  return null;
}

const OTHER: Bound = { kind: "other" };

function nonComment(nodes: Parser.SyntaxNode[]): Parser.SyntaxNode[] {
  return nodes.filter((n) => n.type !== "comment");
}

function classifyValue(value: Parser.SyntaxNode | null): Bound {
  if (!value) return OTHER;
  if (value.type === "list" || value.type === "tuple") {
    const items = nonComment(value.namedChildren);
    const splat = items.some((c) => c.type === "list_splat");
    const size = splat ? null : items.length;
    return { kind: "list", size, strong: size !== null && size > SMALL_LITERAL_MAX };
  }
  if (value.type === "list_comprehension") return { kind: "list", size: null, strong: true, derivedFrom: derivedFromOf(value) };
  if (value.type === "set_comprehension" || value.type === "dictionary_comprehension" || value.type === "generator_expression") {
    return { kind: "other", dict: value.type === "dictionary_comprehension", derivedFrom: derivedFromOf(value) };
  }
  if (value.type === "set" || value.type === "dictionary") {
    const items = nonComment(value.namedChildren);
    const unknown = items.some((c) => c.type === "list_splat" || c.type === "dictionary_splat");
    return { kind: "other", lit: unknown ? null : items.length, dict: value.type === "dictionary" };
  }
  if (value.type === "call") {
    const fn = value.childForFieldName("function");
    const args = nonComment(value.childForFieldName("arguments")?.namedChildren ?? []);
    if (fn?.type === "identifier" && (fn.text === "list" || fn.text === "tuple")) {
      return args.length === 0 ? { kind: "list", size: 0, strong: false } : { kind: "list", size: null, strong: false };
    }
    if (fn?.type === "identifier" && fn.text === "sorted") return { kind: "list", size: null, strong: false };
    if (fn?.type === "attribute") {
      const method = fn.childForFieldName("attribute")?.text;
      if (method === "split" || method === "splitlines") return { kind: "list", size: null, strong: false };
    }
  }
  return OTHER;
}

function classifyType(type: Parser.SyntaxNode | null): Bound | null {
  if (!type) return null;
  return LIST_ANNOTATION.test(type.text.trim()) ? { kind: "list", size: null, strong: false } : OTHER;
}

function isFunctionScopeStop(n: Parser.SyntaxNode, scope: Parser.SyntaxNode): boolean {
  return n.id !== scope.id && (n.type === "function_definition" || n.type === "class_definition" || n.type === "lambda");
}

/** Identifiers contained in an assignment target pattern (tuple unpacking etc.). */
function patternHas(node: Parser.SyntaxNode | null, key: string): boolean {
  if (!node) return false;
  if (node.type === "identifier" || node.type === "attribute") return ws(node.text) === key;
  if (node.type === "pattern_list" || node.type === "tuple_pattern" || node.type === "list_pattern" || node.type === "list_splat_pattern") {
    return node.namedChildren.some((c) => patternHas(c, key));
  }
  return false;
}

/** All bindings of `key` visible in `scope` (function, class or module). Empty array = never bound there. */
function collectBindings(scope: Parser.SyntaxNode, key: string, isSelfChain: boolean): Bound[] {
  const out: Bound[] = [];

  if (!isSelfChain && scope.type === "function_definition") {
    const params = scope.childForFieldName("parameters");
    for (const p of params?.namedChildren ?? []) {
      const nameNode =
        p.type === "identifier" ? p : p.childForFieldName("name") ?? p.namedChildren.find((c) => c.type === "identifier");
      if (!nameNode || nameNode.text !== key) {
        if (!(p.type.endsWith("splat_pattern") && p.namedChildren[0]?.text === key)) continue;
        out.push(OTHER);
        continue;
      }
      if (p.type === "identifier") out.push(OTHER);
      else if (p.type === "typed_parameter" || p.type === "typed_default_parameter") {
        out.push(classifyType(p.childForFieldName("type")) ?? OTHER);
      } else if (p.type === "default_parameter") out.push(classifyValue(p.childForFieldName("value")));
      else out.push(OTHER);
    }
  }

  const visit = (n: Parser.SyntaxNode) => {
    // `self.x` bindings live in methods, so only identifier lookups stop at nested scopes.
    if (!isSelfChain && isFunctionScopeStop(n, scope)) {
      // The name of a nested def/class is itself a binding in this scope.
      if ((n.type === "function_definition" || n.type === "class_definition") && n.childForFieldName("name")?.text === key) {
        out.push(OTHER);
      }
      return;
    }
    if (n.type === "assignment") {
      const left = n.childForFieldName("left");
      if (left && ws(left.text) === key) {
        const annotated = classifyType(n.childForFieldName("type"));
        out.push(annotated ?? classifyValue(n.childForFieldName("right")));
      } else if (patternHas(left, key)) out.push(OTHER);
    } else if (!isSelfChain) {
      if (n.type === "for_statement" && patternHas(n.childForFieldName("left"), key)) out.push(OTHER);
      else if (n.type === "named_expression" && n.childForFieldName("name")?.text === key) out.push(OTHER);
      else if (n.type === "as_pattern") {
        const alias = n.namedChildren.find((c) => c.type === "as_pattern_target");
        if (alias && ws(alias.text) === key) out.push(OTHER);
      } else if (n.type === "aliased_import" && n.childForFieldName("alias")?.text === key) out.push(OTHER);
      else if (n.type === "import_statement" || n.type === "import_from_statement") {
        for (const c of n.namedChildren) if (c.type === "dotted_name" && c.text === key) out.push(OTHER);
      } else if (n.type === "global_statement" || n.type === "nonlocal_statement") {
        if (n.namedChildren.some((c) => c.text === key)) out.push(OTHER);
      }
    }
    for (const child of n.namedChildren) visit(child);
  };
  for (const child of scope.namedChildren) visit(child);
  return out;
}

function enclosingOf(node: Parser.SyntaxNode, types: Set<string>): Parser.SyntaxNode | null {
  for (let c = node.parent; c; c = c.parent) if (types.has(c.type)) return c;
  return null;
}

interface Resolved {
  confidence: Confidence;
}

/** Visible bindings of a name chain plus the scope they were found in. */
function bindingsOf(name: Parser.SyntaxNode, root: Parser.SyntaxNode): { bindings: Bound[]; scope: Parser.SyntaxNode } {
  const key = ws(name.text);
  if (name.type === "attribute") {
    const scope = enclosingOf(name, new Set(["class_definition"])) ?? root;
    return { bindings: collectBindings(scope, key, true), scope };
  }
  const fn = enclosingOf(name, new Set(["function_definition"]));
  let scope = fn ?? root;
  let bindings = fn ? collectBindings(fn, key, false) : [];
  if (bindings.length === 0) {
    scope = root;
    bindings = collectBindings(root, key, false);
  }
  return { bindings, scope };
}

/** Resolve the binding of the tested name; null when unknown, non-list, or too small to flag. */
function resolveBinding(name: Parser.SyntaxNode, root: Parser.SyntaxNode): Resolved | null {
  const { bindings } = bindingsOf(name, root);
  if (bindings.length === 0 || bindings.some((b) => b.kind === "other")) return null;
  const lists = bindings as Extract<Bound, { kind: "list" }>[];
  // Known-small on every binding: not worth a set.
  if (lists.every((b) => b.size !== null && b.size <= SMALL_LITERAL_MAX)) return null;
  return { confidence: lists.every((b) => b.strong) ? "high" : "medium" };
}

/** Does `scope` (a loop) mutate or rebind the collection `key`? */
function mutatesInside(loop: Parser.SyntaxNode, key: string, rebinding = true): boolean {
  let found = false;
  const visit = (n: Parser.SyntaxNode) => {
    if (found) return;
    if (n.type === "call") {
      const fn = n.childForFieldName("function");
      if (fn?.type === "attribute" && MUTATING_METHODS.has(fn.childForFieldName("attribute")?.text ?? "")) {
        const obj = fn.childForFieldName("object");
        if (obj && ws(obj.text) === key) found = true;
      }
    } else if (n.type === "augmented_assignment") {
      if (ws(n.childForFieldName("left")?.text ?? "") === key) found = true;
    } else if (n.type === "assignment") {
      const left = n.childForFieldName("left");
      if (rebinding && left && (ws(left.text) === key || patternHas(left, key))) found = true;
      if (left?.type === "subscript" && ws(left.childForFieldName("value")?.text ?? "") === key) found = true;
    } else if (n.type === "delete_statement") {
      const targets = n.namedChildren.flatMap((c) => (c.type === "expression_list" ? c.namedChildren : [c]));
      for (const t of targets) {
        const base = t.type === "subscript" ? t.childForFieldName("value") : t;
        if (base && ws(base.text) === key) found = true;
      }
    } else if (n.type === "for_statement") {
      if (rebinding && patternHas(n.childForFieldName("left"), key)) found = true;
    } else if (n.type === "named_expression") {
      if (rebinding && n.childForFieldName("name")?.text === key) found = true;
    }
    for (const child of n.namedChildren) visit(child);
  };
  visit(loop);
  return found;
}

interface Context {
  /** Innermost repeated construct (loop or comprehension). */
  node: Parser.SyntaxNode;
  type: LoopType;
  /** Every enclosing repeated loop (not comprehensions), innermost first. */
  loops: Parser.SyntaxNode[];
  /** Iterables per repeated construct, innermost first; null when the trip count is unbounded (while). */
  iterations: (Parser.SyntaxNode[] | null)[];
}

/** The repeated constructs the node sits in, stopping at the enclosing def/lambda/class. */
function repeatedContext(node: Parser.SyntaxNode): Context | null {
  let inner: Parser.SyntaxNode | null = null;
  let type: LoopType = "for";
  const loops: Parser.SyntaxNode[] = [];
  const iterations: (Parser.SyntaxNode[] | null)[] = [];
  let child = node;
  for (let curr = node.parent; curr && !BOUNDARIES.has(curr.type); child = curr, curr = curr.parent) {
    let counts = false;
    if (LOOPS.has(curr.type)) {
      const body = curr.childForFieldName("body");
      if (body && body.id === child.id && !checkRunsAtMostOnce(body)) {
        counts = true;
        loops.push(curr);
        const right = curr.childForFieldName("right");
        iterations.push(curr.type === "for_statement" && right ? [right] : null);
        if (!inner) type = curr.type === "for_statement" ? "for" : "while";
      }
    } else if (COMPREHENSIONS.has(curr.type)) {
      // The iterable of a `for ... in` clause is evaluated once, not per element.
      if (child.type !== "for_in_clause") {
        counts = true;
        const rights = curr.namedChildren.filter((c) => c.type === "for_in_clause").map((c) => c.childForFieldName("right"));
        iterations.push(rights.every((r) => r) ? (rights as Parser.SyntaxNode[]) : null);
      }
    }
    if (counts && !inner) inner = curr;
  }
  return inner ? { node: inner, type, loops, iterations } : null;
}

const SMALL_TRIP_MAX = 8;

function intLiteral(node: Parser.SyntaxNode | undefined): number | null {
  if (!node) return null;
  if (node.type === "integer" && /^\d+$/.test(node.text)) return Number(node.text);
  if (node.type === "unary_operator" && node.text.startsWith("-") && node.namedChildren[0]?.type === "integer" && /^\d+$/.test(node.namedChildren[0].text)) {
    return -Number(node.namedChildren[0].text);
  }
  return null;
}

/** Statically known trip count of one iterable, or null when unknown. */
function tripCount(iter: Parser.SyntaxNode, root: Parser.SyntaxNode): number | null {
  let node = iter;
  while (node.type === "parenthesized_expression" && node.namedChildren[0]) node = node.namedChildren[0];
  if (node.type === "list" || node.type === "tuple" || node.type === "set" || node.type === "dictionary") {
    const items = nonComment(node.namedChildren);
    return items.some((c) => c.type === "list_splat" || c.type === "dictionary_splat") ? null : items.length;
  }
  if (node.type === "call") {
    const fn = node.childForFieldName("function");
    const args = nonComment(node.childForFieldName("arguments")?.namedChildren ?? []);
    if (fn?.type === "identifier" && fn.text === "range" && args.length >= 1 && args.length <= 3) {
      const nums = args.map((a) => intLiteral(a));
      if (nums.some((x) => x === null)) return null;
      const [a, b, c] = nums as number[];
      const [start, stop, step] = args.length === 1 ? [0, a, 1] : [a, b, c ?? 1];
      if (step === 0) return null;
      return Math.max(0, Math.ceil((stop - start) / step));
    }
    if (fn?.type === "attribute" && args.length === 0) {
      const method = fn.childForFieldName("attribute")?.text;
      const obj = fn.childForFieldName("object");
      if ((method === "items" || method === "keys" || method === "values") && obj && isNameChain(obj)) {
        const { bindings, scope } = bindingsOf(obj, root);
        const key = ws(obj.text);
        if (bindings.length === 0 || mutatesInside(scope, key, false)) return null;
        let max = 0;
        let sawBase = false;
        for (const b of bindings) {
          if (b.kind === "other" && b.dict && b.derivedFrom === key) continue; // only filters/maps itself
          if (b.kind !== "other" || !b.dict || b.lit == null) return null;
          sawBase = true;
          max = Math.max(max, b.lit);
        }
        return sawBase ? max : null;
      }
    }
    return null;
  }
  if (isNameChain(node) && (node.type === "identifier" || node.text.startsWith("self."))) {
    const { bindings, scope } = bindingsOf(node, root);
    const key = ws(node.text);
    if (bindings.length === 0 || mutatesInside(scope, key, false)) return null;
    let max = 0;
    let sawBase = false;
    for (const b of bindings) {
      if (b.derivedFrom === key) continue; // only filters/maps itself
      const n = b.kind === "list" ? b.size : b.lit ?? null;
      if (n === null) return null;
      sawBase = true;
      max = Math.max(max, n);
    }
    return sawBase ? max : null;
  }
  return null;
}

/** True when every repeated construct around the test has a statically small trip count. */
function allSmallTrips(ctx: Context, root: Parser.SyntaxNode): boolean {
  return ctx.iterations.every((its) => {
    if (!its) return false;
    let product = 1;
    for (const it of its) {
      const n = tripCount(it, root);
      if (n === null) return false;
      product *= Math.max(n, 1);
    }
    return product <= SMALL_TRIP_MAX;
  });
}
function membershipOperands(cmp: Parser.SyntaxNode): { left: Parser.SyntaxNode; right: Parser.SyntaxNode } | null {
  const operands = nonComment(cmp.namedChildren);
  if (operands.length !== 2) return null; // chained comparisons are out of scope
  const ops = cmp.children.filter((c) => !c.isNamed).map((c) => c.type);
  if (ops.length !== 1 || (ops[0] !== "in" && ops[0] !== "not in")) return null;
  return { left: operands[0], right: operands[1] };
}

/** Name chains we resolve: plain identifiers and `self.attr` chains. */
function isTrackedName(node: Parser.SyntaxNode): boolean {
  if (node.type === "identifier") return true;
  if (!isNameChain(node)) return false;
  let base: Parser.SyntaxNode = node;
  while (base.type === "attribute") base = base.childForFieldName("object")!;
  return base.text === "self";
}

/**
 * Pure function detecting `x in LIST` membership tests repeated inside a loop or
 * comprehension (CODE-C5.1). Parses only; never imports or executes client code.
 */
export function detectListMembershipInLoop(
  rootNode: Parser.SyntaxNode,
  filePath: string,
  sourceLines: string[]
): Finding[] {
  const findings: Finding[] = [];
  const ordinals = new Map<string, number>();

  const visit = (n: Parser.SyntaxNode) => {
    if (n.type === "comparison_operator") {
      const finding = examine(n);
      if (finding) findings.push(finding);
    }
    for (const child of n.namedChildren) visit(child);
  };

  const examine = (cmp: Parser.SyntaxNode): Finding | null => {
    const operands = membershipOperands(cmp);
    if (!operands) return null;
    const target = operands.right;
    if (!isTrackedName(target)) return null;

    const ctx = repeatedContext(cmp);
    if (!ctx) return null;

    const key = ws(target.text);
    if (ctx.loops.some((loop) => mutatesInside(loop, key))) return null;

    if (allSmallTrips(ctx, rootNode)) return null;

    const resolved = resolveBinding(target, rootNode);
    if (!resolved) return null;

    const ctxRow = ctx.node.startPosition.row;
    const siteRow = cmp.startPosition.row;
    if ([ctxRow, siteRow].some((r) => isLineSuppressed(sourceLines[r] ?? "", [CHECK]).isSuppressed)) return null;

    const qualname = getEnclosingQualname(cmp);
    const ordKey = `${qualname}:${key}`;
    const ordinal = ordinals.get(ordKey) ?? 0;
    ordinals.set(ordKey, ordinal + 1);

    const op = cmp.children.some((c) => c.type === "not in") ? "not in" : "in";
    const header = (sourceLines[ctxRow] ?? "").trim().replace(/:$/, "");
    const siteText = (sourceLines[siteRow] ?? "").trim();
    const where = ctx.node.type === "for_statement" || ctx.node.type === "while_statement" ? `loop '${header}'` : `comprehension at line ${ctxRow + 1}`;

    return {
      check: CHECK,
      kind: KIND,
      fingerprint: generateFingerprint(CHECK, KIND, filePath, `${ordKey}:${ordinal}`),
      identity: `${KIND}:${ordKey}:${ordinal}`,
      location: { path: filePath, startLine: siteRow + 1, endLine: cmp.endPosition.row + 1 },
      evidence: {
        snippet: `${header}:\n${siteText}`,
        symbol: key,
        expr: cmp.text.replace(/\s+/g, " "),
        loopType: ctx.type,
        suggested: `set(${key})`,
      },
      why: `'${op} ${key}' is evaluated on every iteration of ${where}, but '${key}' is a list or tuple, so each test scans it linearly (O(n)) and the whole loop is O(n*m); a set built once makes each test O(1).`,
      severity: "medium",
      confidence: resolved.confidence,
      limitations: [...LIMITATIONS],
      evidenceTier: "static",
      impact: {
        quantified: false,
        reason: "Cost = collection size x trip count; neither is measured statically.",
      },
      references: REFERENCES.map((r) => ({ ...r })),
      agentPrompt: `In ${filePath}:${siteRow + 1} (${where}), build \`${key}_set = set(${key})\` once before the loop and test membership against it, but only if the elements are hashable and '${key}' is not modified inside the loop. Static finding only - keep the change minimal and covered by tests.`,
      detector: { id: "owner-a-static-scan", version: DETECTOR_VERSION },
    };
  };

  visit(rootNode);
  return findings.sort((a, b) => a.location.startLine - b.location.startLine);
}


