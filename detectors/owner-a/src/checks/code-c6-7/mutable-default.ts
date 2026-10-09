import Parser from "tree-sitter";
import { Confidence, Finding, generateFingerprint } from "../../core/finding.js";
import { isLineSuppressed } from "../../core/suppressions.js";
import { getEnclosingQualname } from "../../core/loops.js";

const CHECK = "CODE-C6.7";
const KIND = "mutable-default-mutated";
const DETECTOR_VERSION = "0.1.0";
const SUPPRESSION_CODES = [CHECK, "B006"];

const REFERENCES = [
  {
    id: "SRC-01",
    title: "Watts This Smell: A Comprehensive Taxonomy of Software Energy Smells",
    url: "https://arxiv.org/abs/2604.04809",
  },
  {
    id: "taxonomy-c6.7",
    title: "Software Compute Waste Taxonomy - C6.7 Leaking mutable defaults",
    url: "https://github.com/AWS-env/environmental-hacks/issues/85",
  },
  {
    id: "ruff-B006",
    title: "Ruff B006 mutable-argument-default",
    url: "https://docs.astral.sh/ruff/rules/mutable-argument-default/",
  },
  {
    id: "pylint-W0102",
    title: "Pylint W0102 dangerous-default-value",
    url: "https://pylint.readthedocs.io/en/stable/user_guide/messages/warning/dangerous-default-value.html",
  },
  {
    id: "codeql-py-modification-of-default-value",
    title: "CodeQL py/modification-of-default-value",
    url: "https://codeql.github.com/codeql-query-help/python/py-modification-of-default-value/",
  },
];

const MUTATING_METHODS = new Set([
  "append", "extend", "insert", "add", "update", "setdefault", "pop", "popitem",
  "remove", "discard", "clear", "sort", "reverse", "appendleft",
]);
const MUTABLE_CALLEES = new Set([
  "list", "dict", "set", "defaultdict", "collections.defaultdict", "deque",
  "collections.deque", "bytearray",
]);
const LITERAL_TYPES = new Set(["list", "dictionary", "set"]);
const COMPREHENSION_TYPES = new Set(["list_comprehension", "dictionary_comprehension", "set_comprehension"]);
const NESTED_SCOPES = new Set(["function_definition", "lambda"]);
/** Outermost annotation names that declare a read-only / immutable parameter. */
const IMMUTABLE_ANNOTATIONS = new Set([
  "Final", "tuple", "Tuple", "frozenset", "FrozenSet", "Sequence", "Mapping",
  "AbstractSet", "Iterable", "Collection", "ReadOnly",
]);
const MEMO_NAME = /^_?(cache|memo|memoize|seen|visited)$/;

interface Mutation {
  site: Parser.SyntaxNode;
  mutator: string;
  byMethod: boolean;
}

function isMutableDefault(value: Parser.SyntaxNode): "literal" | "other" | null {
  if (LITERAL_TYPES.has(value.type)) return "literal";
  if (COMPREHENSION_TYPES.has(value.type)) return "other";
  if (value.type === "call") {
    const fn = value.childForFieldName("function")?.text.replace(/\s+/g, "") ?? "";
    if (MUTABLE_CALLEES.has(fn)) return "other";
  }
  return null;
}

function isImmutableAnnotation(type: Parser.SyntaxNode | null): boolean {
  if (!type) return false;
  const head = type.text.trim().match(/^[A-Za-z_][\w.]*/)?.[0] ?? "";
  const outer = head.split(".").pop() ?? "";
  return IMMUTABLE_ANNOTATIONS.has(outer);
}

function paramNames(fn: Parser.SyntaxNode): Set<string> {
  const names = new Set<string>();
  for (const p of fn.childForFieldName("parameters")?.namedChildren ?? []) {
    if (p.type === "list_splat_pattern" || p.type === "dictionary_splat_pattern") {
      const inner = p.namedChildren[0];
      if (inner) names.add(inner.text);
      continue;
    }
    const id =
      p.type === "identifier"
        ? p
        : p.childForFieldName("name") ?? p.namedChildren.find((c) => c.type === "identifier");
    if (id) names.add(id.text);
  }
  return names;
}

function targetNames(target: Parser.SyntaxNode | null, out: Set<string>): void {
  if (!target) return;
  if (target.type === "identifier") out.add(target.text);
  else if (
    ["pattern_list", "tuple_pattern", "list_pattern", "tuple", "list", "list_splat_pattern", "parenthesized_expression"].includes(
      target.type
    )
  ) {
    for (const c of target.namedChildren) targetNames(c, out);
  }
}

/** Does a nested scope bind `name` itself (parameter or local assignment, without nonlocal/global)? */
function shadows(scope: Parser.SyntaxNode, name: string): boolean {
  if (paramNames(scope).has(name)) return true;
  let declared = false;
  let assigned = false;
  const walk = (n: Parser.SyntaxNode) => {
    if (n.type === "nonlocal_statement" || n.type === "global_statement") {
      if (n.namedChildren.some((c) => c.text === name)) declared = true;
    } else if (n.type === "assignment") {
      const s = new Set<string>();
      targetNames(n.childForFieldName("left"), s);
      if (s.has(name)) assigned = true;
    }
    if (n !== scope && (NESTED_SCOPES.has(n.type) || n.type === "class_definition")) return;
    for (const c of n.namedChildren) walk(c);
  };
  walk(scope.childForFieldName("body") ?? scope);
  return assigned && !declared;
}

/**
 * First mutation of `name` in source order that happens before any plain re-binding
 * of the name in the same scope. A re-binding anywhere earlier in the body
 * (`p = list(p)`, `if p is None: p = []`) means later mutations touch a fresh object.
 */
function firstMutation(body: Parser.SyntaxNode, name: string): Mutation | null {
  let rebound = false;
  let found: Mutation | null = null;
  const isName = (n: Parser.SyntaxNode | null) => n?.type === "identifier" && n.text === name;

  const visit = (n: Parser.SyntaxNode, nested: boolean): void => {
    if (found) return;
    if (n.type === "class_definition") return;
    if (NESTED_SCOPES.has(n.type)) {
      if (shadows(n, name)) return;
      const inner = n.childForFieldName("body");
      if (inner) visit(inner, true);
      return;
    }
    switch (n.type) {
      case "assignment": {
        const left = n.childForFieldName("left");
        const right = n.childForFieldName("right");
        if (right) visit(right, nested);
        if (found) return;
        if (left?.type === "subscript" && isName(left.childForFieldName("value"))) {
          if (!rebound) found = { site: n, mutator: "item-assign", byMethod: false };
          return;
        }
        if (!nested) {
          const s = new Set<string>();
          targetNames(left, s);
          if (s.has(name)) rebound = true;
        }
        return;
      }
      case "augmented_assignment": {
        const left = n.childForFieldName("left");
        const op = n.childForFieldName("operator")?.text;
        const right = n.childForFieldName("right");
        if (right) visit(right, nested);
        if (found) return;
        if (!rebound && left?.type === "subscript" && isName(left.childForFieldName("value"))) {
          found = { site: n, mutator: "item-assign", byMethod: false };
        } else if (!rebound && isName(left) && op === "+=") {
          found = { site: n, mutator: "+=", byMethod: false };
        }
        return;
      }
      case "delete_statement": {
        const targets = n.namedChildren.flatMap((c) => (c.type === "expression_list" ? c.namedChildren : [c]));
        if (!rebound && targets.some((t) => t.type === "subscript" && isName(t.childForFieldName("value")))) {
          found = { site: n, mutator: "del", byMethod: false };
        }
        return;
      }
      case "for_statement": {
        const iter = n.childForFieldName("right");
        const target = n.childForFieldName("left");
        if (iter) visit(iter, nested);
        if (!nested) {
          const s = new Set<string>();
          targetNames(target, s);
          if (s.has(name)) rebound = true;
        }
        for (const c of n.namedChildren) if (c.id !== iter?.id && c.id !== target?.id) visit(c, nested);
        return;
      }
      case "named_expression": {
        const value = n.childForFieldName("value");
        if (value) visit(value, nested);
        if (!nested && isName(n.childForFieldName("name"))) rebound = true;
        return;
      }
      case "call": {
        const fn = n.childForFieldName("function");
        const method = fn?.type === "attribute" ? fn.childForFieldName("attribute")?.text ?? "" : "";
        if (!rebound && fn?.type === "attribute" && isName(fn.childForFieldName("object")) && MUTATING_METHODS.has(method)) {
          found = { site: n, mutator: method, byMethod: true };
          return;
        }
        break;
      }
    }
    for (const c of n.namedChildren) visit(c, nested);
  };
  visit(body, false);
  return found;
}

/**
 * Pure function detecting mutable default arguments that the function body mutates
 * (CODE-C6.7). Never performs I/O or executes code.
 */
export function detectLeakingMutableDefaults(
  rootNode: Parser.SyntaxNode,
  filePath: string,
  sourceLines: string[]
): Finding[] {
  const findings: Finding[] = [];
  const ordinals = new Map<string, number>();

  const checkFunction = (fn: Parser.SyntaxNode) => {
    const body = fn.childForFieldName("body");
    const params = fn.childForFieldName("parameters");
    if (!body || !params) return;
    const parent = getEnclosingQualname(fn);
    const ownName = fn.type === "lambda" ? "<lambda>" : fn.childForFieldName("name")?.text ?? "<anonymous>";
    const qualname = parent === "<module>" ? ownName : `${parent}.${ownName}`;

    for (const p of params.namedChildren) {
      if (p.type !== "default_parameter" && p.type !== "typed_default_parameter") continue;
      const nameNode = p.childForFieldName("name");
      const value = p.childForFieldName("value");
      if (!nameNode || !value || nameNode.type !== "identifier") continue;
      const form = isMutableDefault(value);
      if (!form) continue;
      if (isImmutableAnnotation(p.childForFieldName("type"))) continue;

      const name = nameNode.text;
      const mutation = firstMutation(body, name);
      if (!mutation) continue;

      const defRow = fn.startPosition.row;
      const mutRow = mutation.site.startPosition.row;
      const paramRow = p.startPosition.row;
      if ([defRow, paramRow, mutRow].some((r) => isLineSuppressed(sourceLines[r] ?? "", SUPPRESSION_CODES).isSuppressed)) {
        continue;
      }

      const baseKey = `${qualname}:${name}`;
      const ordinal = ordinals.get(baseKey) ?? 0;
      ordinals.set(baseKey, ordinal + 1);
      const identity = `${KIND}:${baseKey}${ordinal > 0 ? `:${ordinal}` : ""}`;

      const memo = MEMO_NAME.test(name);
      const confidence: Confidence = memo ? "low" : form === "literal" && mutation.byMethod ? "high" : "medium";
      const limitations = [
        "Static only: how often the function is called, or how large the shared default grows, is not measured.",
        "This finding rests on Python's default-argument semantics (the default is evaluated once, at definition time) and on the Ruff B006, Pylint W0102 and CodeQL py/modification-of-default-value documentation, not on SRC-01, whose Python data did not observe this smell.",
        "Only mutations visible in the function body are detected; mutation through an alias or a helper call is not seen.",
      ];
      if (memo) {
        limitations.push(
          `Parameter '${name}' is named like a cache; a mutable default used as a deliberate memo is an intentional (if fragile) idiom, so this may be intended.`
        );
      }

      const defLine = (sourceLines[defRow] ?? "").trim();
      const mutLine = (sourceLines[mutRow] ?? "").trim();
      const snippet = defRow === mutRow ? defLine : `${defLine}\n${mutLine}`;
      const defaultText = value.text.trim();

      findings.push({
        check: CHECK,
        kind: KIND,
        fingerprint: generateFingerprint(CHECK, KIND, filePath, `${baseKey}:${ordinal}`),
        identity,
        location: { path: filePath, startLine: mutRow + 1, endLine: mutation.site.endPosition.row + 1 },
        evidence: {
          snippet,
          symbol: name,
          mutator: mutation.mutator,
        },
        why: `Parameter '${name}' of '${qualname}' has a mutable default (${defaultText}) that the body mutates (${mutation.mutator}); the default object is created once, so the mutation persists and accumulates across calls.`,
        severity: "medium",
        confidence,
        limitations,
        evidenceTier: "static",
        impact: {
          quantified: false,
          reason: "Growth depends on call count and the size of each mutation; neither is measured statically.",
        },
        references: REFERENCES.map((r) => ({ ...r })),
        agentPrompt: `In ${filePath}:${paramRow + 1}, change the default of '${name}' to \`None\` and create the collection inside the function (\`if ${name} is None: ${name} = ${defaultText}\`) so each call gets a fresh object. Keep behaviour otherwise identical; static finding only, cover with a test that calls the function twice.`,
        detector: { id: "owner-a-static-scan", version: DETECTOR_VERSION },
      });
    }
  };

  const visit = (n: Parser.SyntaxNode) => {
    if (n.type === "function_definition" || n.type === "lambda") checkFunction(n);
    for (const c of n.namedChildren) visit(c);
  };
  visit(rootNode);
  return findings.sort((a, b) => a.location.startLine - b.location.startLine);
}
