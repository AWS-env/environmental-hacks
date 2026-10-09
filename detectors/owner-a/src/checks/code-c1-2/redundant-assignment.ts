import Parser from "tree-sitter";
import { Confidence, Finding, Severity, generateFingerprint } from "../../core/finding.js";
import { isLineSuppressed } from "../../core/suppressions.js";
import { getEnclosingQualname } from "../../core/loops.js";

const CHECK = "CODE-C1.2";
const SELF_KIND = "self-assignment";
const DEAD_KIND = "dead-store";
const DETECTOR_VERSION = "0.1.0";

const REFERENCES = [
  {
    id: "SRC-01",
    title:
      "Watts This Smell: An Empirical Study on Energy Smells in Python Software",
    url: "https://arxiv.org/abs/2604.04809",
  },
  {
    id: "taxonomy-c1.2",
    title: "Software Compute Waste Taxonomy — C1.2 Redundant assignment",
    url: "https://github.com/AWS-env/environmental-hacks/issues/35",
  },
];

const NEGLIGIBLE_ALONE =
  "Static only: one redundant statement is negligible; it matters when the code runs in a hot loop, and trip count is not measured.";
const SETTER_SIDE_EFFECTS =
  "Property setters, `__setattr__` and `__getitem__`/`__setitem__` (e.g. `defaultdict` inserting a missing key) can make this assignment observable; confirm the target is a plain attribute or container.";
const KEEP_THE_CALL =
  "The discarded right-hand side runs code; drop the binding but keep the expression as a bare statement if its side effects are needed.";

/** Calls that let a function read its own locals by name (frame introspection). */
const FRAME_ACCESS = new Set(["locals", "eval", "exec", "currentframe", "_getframe"]);

/** Nodes that start a new Python scope (their bodies are analysed separately). */
const SCOPE_TYPES = new Set(["function_definition", "class_definition", "lambda"]);

/** Right-hand-side nodes that run code beyond plain value construction. */
const EFFECT_TYPES = new Set(["call", "await", "yield", "named_expression"]);
/** Empty built-in constructors (`set()`, `dict()`) build a value like a literal does. */
const EMPTY_CONSTRUCTORS = new Set([
  "set",
  "list",
  "dict",
  "tuple",
  "frozenset",
  "str",
  "bytes",
  "bytearray",
  "int",
  "float",
  "bool",
]);
/** Right-hand-side nodes that may run user code (`__getattr__`, `__getitem__`). */
const LOOKUP_TYPES = new Set(["attribute", "subscript"]);

const JUMP_TYPES = new Set(["break_statement", "continue_statement"]);
const TERMINAL_TYPES = new Set([...JUMP_TYPES, "return_statement", "raise_statement"]);
const INTERPOLATION = new Set(["interpolation"]);

const SCALAR_LITERALS = new Set(["true", "false", "none", "integer", "float"]);

interface Assignment {
  stmt: Parser.SyntaxNode;
  left: Parser.SyntaxNode;
  right: Parser.SyntaxNode;
  annotation: Parser.SyntaxNode | null;
}

interface Statement {
  node: Parser.SyntaxNode;
  assignment: Assignment | null;
  /** Names the statement reads or binds (attribute fields and keyword names excluded). */
  mentions: Set<string>;
  /** Contains `break` / `continue`, so control may skip the rest of the block. */
  jumps: boolean;
  terminal: boolean;
}

function statementsOf(block: Parser.SyntaxNode | null): Parser.SyntaxNode[] {
  return block ? block.namedChildren.filter((c) => c.type !== "comment") : [];
}

/** `left = right` (optionally annotated) as a whole statement; chained `a = b = c` excluded. */
function plainAssignment(stmt: Parser.SyntaxNode): Assignment | null {
  if (stmt.type !== "expression_statement") return null;
  const inner = statementsOf(stmt);
  if (inner.length !== 1 || inner[0].type !== "assignment") return null;
  const left = inner[0].childForFieldName("left");
  const right = inner[0].childForFieldName("right");
  if (!left || !right || right.type === "assignment") return null;
  return { stmt, left, right, annotation: inner[0].childForFieldName("type") };
}

function contains(node: Parser.SyntaxNode, types: Set<string>): boolean {
  if (types.has(node.type)) return true;
  return node.namedChildren.some((c) => contains(c, types));
}

/** Leaf tokens, so `a[ i ]` matches `a[i]` but `"x y"` never matches `"xy"`. */
function tokens(node: Parser.SyntaxNode): string[] {
  if (node.childCount === 0) return node.type === "comment" ? [] : [node.text];
  return node.children.flatMap(tokens);
}

function sameTokens(a: Parser.SyntaxNode, b: Parser.SyntaxNode): boolean {
  const ta = tokens(a);
  const tb = tokens(b);
  return ta.length === tb.length && ta.every((t, i) => t === tb[i]);
}

function normalized(node: Parser.SyntaxNode): string {
  return node.text.replace(/\s+/g, " ").trim();
}

/** An identifier in name position: not `obj.<attr>` and not the `<name>=` of a keyword argument. */
function isNameUse(id: Parser.SyntaxNode): boolean {
  const parent = id.parent;
  if (parent?.type === "attribute" && parent.childForFieldName("attribute")?.id === id.id) {
    return false;
  }
  if (parent?.type === "keyword_argument" && parent.childForFieldName("name")?.id === id.id) {
    return false;
  }
  return true;
}

function mentionsOf(node: Parser.SyntaxNode | null, into = new Set<string>()): Set<string> {
  if (!node) return into;
  if (node.type === "identifier" && isNameUse(node)) into.add(node.text);
  for (const child of node.namedChildren) mentionsOf(child, into);
  return into;
}

function isLiteral(node: Parser.SyntaxNode): boolean {
  if (SCALAR_LITERALS.has(node.type)) return true;
  if (node.type === "string") return !contains(node, INTERPOLATION);
  if (node.type === "concatenated_string") return node.namedChildren.every(isLiteral);
  if (["list", "tuple", "dictionary", "set"].includes(node.type)) {
    return node.namedChildCount === 0;
  }
  if (node.type === "unary_operator") {
    const arg = node.childForFieldName("argument");
    return arg !== null && (arg.type === "integer" || arg.type === "float");
  }
  return false;
}

function isEmptyConstructor(node: Parser.SyntaxNode): boolean {
  if (node.type !== "call") return false;
  const callee = node.childForFieldName("function");
  return (
    callee?.type === "identifier" &&
    EMPTY_CONSTRUCTORS.has(callee.text) &&
    node.childForFieldName("arguments")?.namedChildCount === 0
  );
}

/** Nearest enclosing scope node: function, class, lambda or module. */
function scopeOf(node: Parser.SyntaxNode): Parser.SyntaxNode | null {
  let curr = node.parent;
  while (curr && !SCOPE_TYPES.has(curr.type) && curr.type !== "module") curr = curr.parent;
  return curr;
}

function enclosingLoop(node: Parser.SyntaxNode, scope: Parser.SyntaxNode): "for" | "while" | undefined {
  for (let curr = node.parent; curr && curr.id !== scope.id; curr = curr.parent) {
    if (curr.type === "for_statement") return "for";
    if (curr.type === "while_statement") return "while";
  }
  return undefined;
}

/** Inside a `try` or `with` (within the function), an exception can keep the old value alive. */
function exceptionCanEscape(block: Parser.SyntaxNode, fn: Parser.SyntaxNode): boolean {
  for (let curr = block.parent; curr && curr.id !== fn.id; curr = curr.parent) {
    if (curr.type === "try_statement" || curr.type === "with_statement") return true;
  }
  return false;
}

function suppressed(sourceLines: string[], rows: number[]): boolean {
  return rows.some((r) => isLineSuppressed(sourceLines[r] ?? "", [CHECK]).isSuppressed);
}

/** Names a function cannot safely treat as private locals, or null to skip the function. */
function unsafeNames(fn: Parser.SyntaxNode): Set<string> | null {
  const names = new Set<string>();
  let frameAccess = false;
  const visit = (node: Parser.SyntaxNode) => {
    if (node.id !== fn.id && SCOPE_TYPES.has(node.type)) {
      // Captured by a closure: a call between the stores may read it.
      mentionsOf(node, names);
      return;
    }
    if (node.type === "global_statement" || node.type === "nonlocal_statement") {
      mentionsOf(node, names);
    }
    if (node.type === "call") {
      const callee = node.childForFieldName("function")?.text.split(".").pop() ?? "";
      const args = node.childForFieldName("arguments");
      if (FRAME_ACCESS.has(callee) || (callee === "vars" && args?.namedChildCount === 0)) {
        frameAccess = true;
      }
    }
    for (const child of node.namedChildren) visit(child);
  };
  const body = fn.childForFieldName("body");
  if (body) visit(body);
  return frameAccess ? null : names;
}

/** Blocks whose statements run in `fn`'s own scope (nested defs/classes excluded). */
function ownBlocks(fn: Parser.SyntaxNode): Parser.SyntaxNode[] {
  const blocks: Parser.SyntaxNode[] = [];
  const visit = (node: Parser.SyntaxNode) => {
    if (SCOPE_TYPES.has(node.type)) return;
    if (node.type === "block") blocks.push(node);
    for (const child of node.namedChildren) visit(child);
  };
  const body = fn.childForFieldName("body");
  if (body) visit(body);
  return blocks;
}

function analyseStatement(node: Parser.SyntaxNode): Statement {
  const terminal = TERMINAL_TYPES.has(node.type);
  return {
    node,
    assignment: plainAssignment(node),
    mentions: mentionsOf(node),
    jumps: contains(node, JUMP_TYPES),
    terminal,
  };
}

/** `name` is a parameter or appears in the function body before `stmt`. */
function boundBefore(fn: Parser.SyntaxNode, name: string, stmt: Parser.SyntaxNode): boolean {
  const params = fn.childForFieldName("parameters");
  if (params && mentionsOf(params).has(name)) return true;
  return fn
    .childForFieldName("body")!
    .descendantsOfType("identifier")
    .some((id) => id.text === name && id.startIndex < stmt.startIndex);
}

/** `x = x`, `a, b = a, b`, `self.n = self.n`, `row[i] = row[i]`. */
function selfAssignmentForm(a: Assignment): "name" | "lookup" | null {
  if (a.annotation) return null;
  const { left, right } = a;
  if (left.type === "identifier") return sameTokens(left, right) ? "name" : null;
  if (left.type === "attribute" || left.type === "subscript") {
    if (contains(left, EFFECT_TYPES)) return null; // `get().x = get().x` runs get() twice
    return sameTokens(left, right) ? "lookup" : null;
  }
  const targets = ["pattern_list", "tuple_pattern"].includes(left.type) ? left.namedChildren : null;
  const values = ["expression_list", "tuple"].includes(right.type) ? right.namedChildren : null;
  if (!targets || !values || targets.length !== values.length || targets.length === 0) return null;
  const pairwise = targets.every((t, i) => t.type === "identifier" && sameTokens(t, values[i]));
  return pairwise ? "name" : null;
}

interface Ordinals {
  next(key: string): number;
}

function ordinals(): Ordinals {
  const seen = new Map<string, number>();
  return {
    next(key) {
      const n = seen.get(key) ?? 0;
      seen.set(key, n + 1);
      return n;
    },
  };
}

function baseFinding(
  kind: string,
  filePath: string,
  key: string,
  ordinal: number
): Pick<Finding, "check" | "kind" | "fingerprint" | "identity"> {
  return {
    check: CHECK,
    kind,
    fingerprint: generateFingerprint(CHECK, kind, filePath, `${key}:${ordinal}`),
    identity: `${kind}:${key}:${ordinal}`,
  };
}

function shared(): Pick<Finding, "evidenceTier" | "impact" | "references" | "detector"> {
  return {
    evidenceTier: "static",
    impact: {
      quantified: false,
      reason:
        "Saved work = cost of the redundant statement × how often it runs; neither is measured statically.",
    },
    references: REFERENCES.map((r) => ({ ...r })),
    detector: { id: "owner-a-static-scan", version: DETECTOR_VERSION },
  };
}

function detectSelfAssignments(
  rootNode: Parser.SyntaxNode,
  filePath: string,
  sourceLines: string[]
): Finding[] {
  const findings: Finding[] = [];
  const counter = ordinals();

  /**
   * `name = name` is a no-op only for a function local that is already bound.
   * In a module or class body it copies a builtin or outer value into that
   * namespace (`TimeoutError = TimeoutError` re-exports it); an unbound local
   * raises UnboundLocalError, a bug rather than waste.
   */
  const rebindsNothing = (a: Assignment, form: "name" | "lookup", scope: Parser.SyntaxNode) => {
    if (form === "lookup") return true;
    if (scope.type !== "function_definition") return false;
    return [...mentionsOf(a.left)].every((name) => boundBefore(scope, name, a.stmt));
  };

  const visit = (node: Parser.SyntaxNode) => {
    const a = plainAssignment(node);
    const scope = a ? scopeOf(node) : null;
    if (a && scope && scope.type !== "class_definition") {
      const form = selfAssignmentForm(a);
      const rows = [node.startPosition.row, node.endPosition.row];
      if (form && rebindsNothing(a, form, scope) && !suppressed(sourceLines, rows)) {
        const statement = normalized(node);
        const qualname = getEnclosingQualname(node);
        const key = `${qualname}:${statement}`;
        const target = a.left.text;
        const startLine = node.startPosition.row + 1;
        const endLine = node.endPosition.row + 1;
        const loopType = enclosingLoop(node, scope);
        findings.push({
          ...baseFinding(SELF_KIND, filePath, key, counter.next(key)),
          location: { path: filePath, startLine, endLine },
          evidence: {
            snippet: (sourceLines[node.startPosition.row] ?? "").trim(),
            symbol: target,
            ...(loopType ? { loopType } : {}),
          },
          why: `\`${statement}\` assigns '${target}' to itself, leaving state unchanged${
            loopType ? ` on every iteration of the enclosing ${loopType} loop` : ""
          }.`,
          severity: "low",
          confidence: form === "name" ? "high" : "medium",
          limitations: form === "name" ? [NEGLIGIBLE_ALONE] : [NEGLIGIBLE_ALONE, SETTER_SIDE_EFFECTS],
          ...shared(),
          agentPrompt: `In ${filePath}:${startLine}, remove the self-assignment \`${statement}\`${
            form === "lookup" ? ` after confirming '${target}' has no property setter or __setitem__ side effects` : ""
          }. Static finding only — keep the change minimal and covered by tests.`,
        });
      }
    }
    for (const child of node.namedChildren) visit(child);
  };

  visit(rootNode);
  return findings;
}

/** A local store `x = …` overwritten by a later `x = …` in the same block before any read. */
function detectDeadStores(
  rootNode: Parser.SyntaxNode,
  filePath: string,
  sourceLines: string[]
): Finding[] {
  const findings: Finding[] = [];
  const counter = ordinals();

  for (const fn of rootNode.descendantsOfType("function_definition")) {
    const unsafe = unsafeNames(fn);
    if (!unsafe) continue; // locals()/eval()/frame access can read any local

    for (const block of ownBlocks(fn)) {
      const stmts = statementsOf(block).map(analyseStatement);
      const guarded = exceptionCanEscape(block, fn);

      stmts.forEach((store, i) => {
        const a = store.assignment;
        if (!a || a.left.type !== "identifier" || selfAssignmentForm(a)) return;
        const name = a.left.text;
        if (/^_+$/.test(name) || unsafe.has(name)) return;

        let overwrite: Statement | null = null;
        for (const later of stmts.slice(i + 1)) {
          const b = later.assignment;
          if (b && b.left.type === "identifier" && b.left.text === name) {
            const reads = mentionsOf(b.right, mentionsOf(b.annotation));
            if (!reads.has(name)) overwrite = later;
            break;
          }
          if (later.mentions.has(name) || later.terminal || later.jumps) break;
        }
        if (!overwrite) return;
        const b = overwrite.assignment!;

        // In a try/with an exception between the two stores keeps the first
        // value; only an adjacent literal overwrite cannot raise.
        if (guarded && (stmts[i + 1] !== overwrite || !isLiteral(b.right))) return;

        // `x = None` before reloading x releases the old value first (lower peak memory).
        if (a.right.type === "none" && contains(b.right, EFFECT_TYPES) && boundBefore(fn, name, a.stmt)) {
          return;
        }

        const rows = [a.stmt.startPosition.row, a.stmt.endPosition.row, b.stmt.startPosition.row];
        if (suppressed(sourceLines, rows)) return;

        const effectful = !isEmptyConstructor(a.right) && contains(a.right, EFFECT_TYPES);
        const lookup = !effectful && contains(a.right, LOOKUP_TYPES);
        const severity: Severity = effectful ? "medium" : "low";
        const confidence: Confidence = effectful || lookup ? "medium" : "high";
        const limitations = [NEGLIGIBLE_ALONE];
        if (effectful || lookup) limitations.push(KEEP_THE_CALL);

        const storeText = normalized(a.stmt);
        const overwriteText = normalized(b.stmt);
        const overwriteLine = b.stmt.startPosition.row + 1;
        const qualname = getEnclosingQualname(a.stmt);
        const key = `${qualname}:${name}:${storeText}`;
        const startLine = a.stmt.startPosition.row + 1;
        const loopType = enclosingLoop(a.stmt, fn);

        const prompt = effectful || lookup
          ? `drop the binding in \`${storeText}\` ('${name}' is overwritten at line ${overwriteLine} by \`${overwriteText}\` before it is read); keep \`${normalized(a.right)}\` as a bare statement only if its side effects are needed, otherwise delete the line`
          : `delete \`${storeText}\`: '${name}' is overwritten at line ${overwriteLine} by \`${overwriteText}\` before it is read${
              a.annotation ? " (move the annotation onto the later assignment)" : ""
            }`;

        findings.push({
          ...baseFinding(DEAD_KIND, filePath, key, counter.next(key)),
          location: { path: filePath, startLine, endLine: b.stmt.endPosition.row + 1 },
          evidence: {
            snippet: (sourceLines[a.stmt.startPosition.row] ?? "").trim(),
            symbol: name,
            expr: overwriteText,
            ...(loopType ? { loopType } : {}),
          },
          why: `'${name}' is assigned by \`${storeText}\` and overwritten at line ${overwriteLine} before it is read, so the first value${
            effectful ? " (computed by a call)" : ""
          } is wasted${loopType ? ` on every iteration of the enclosing ${loopType} loop` : ""}.`,
          severity,
          confidence,
          limitations,
          ...shared(),
          agentPrompt: `In ${filePath}:${startLine}, ${prompt}. Static finding only — keep the change minimal and covered by tests.`,
        });
      });
    }
  }

  return findings;
}

/**
 * Pure function detecting statements that leave state unchanged (CODE-C1.2):
 * self-assignments and local stores overwritten before they are read.
 * Never performs I/O or executes code.
 */
export function detectRedundantAssignments(
  rootNode: Parser.SyntaxNode,
  filePath: string,
  sourceLines: string[]
): Finding[] {
  return [
    ...detectSelfAssignments(rootNode, filePath, sourceLines),
    ...detectDeadStores(rootNode, filePath, sourceLines),
  ].sort((a, b) => a.location.startLine - b.location.startLine);
}
