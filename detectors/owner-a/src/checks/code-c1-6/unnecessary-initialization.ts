import Parser from "tree-sitter";
import { Confidence, Finding, Severity, generateFingerprint } from "../../core/finding.js";
import { isLineSuppressed } from "../../core/suppressions.js";
import {
  SetupSignal,
  collectImportAliases,
  getEnclosingQualname,
  isTrivialBuiltinCall,
  isVolatileCall,
  resolveCallee,
  setupSignalFor,
} from "../../core/loops.js";

const CHECK = "CODE-C1.6";
const OVERWRITTEN_KIND = "overwritten-init";
const EARLY_EXIT_KIND = "init-before-early-exit";
const DETECTOR_VERSION = "0.1.0";

const REFERENCES = [
  {
    id: "SRC-01",
    title:
      "Watts This Smell: An Empirical Study on Energy Smells in Python Software",
    url: "https://arxiv.org/abs/2604.04809",
  },
  {
    id: "taxonomy-c1.6",
    title: "Software Compute Waste Taxonomy — C1.6 Unnecessary initialization",
    url: "https://github.com/AWS-env/environmental-hacks/issues/39",
  },
];

const PATH_SHARE =
  "Static only: how often the wasted path runs (early-exit rate, branch frequency, trip count) is not measured.";
const KEEP_THE_CALL =
  "The setup runs code; drop the binding but keep the call as a bare statement if its side effects are needed.";
const CALL_ORDER =
  "The setup runs code; moving it below the guard changes when it runs, so confirm the guard and the statements before it do not depend on its side effects.";

/** Calls that let a function read its own locals by name (frame introspection). */
const FRAME_ACCESS = new Set(["locals", "eval", "exec", "currentframe", "_getframe"]);
/** Nodes that start a new Python scope (their bodies are analysed separately). */
const SCOPE_TYPES = new Set(["function_definition", "class_definition", "lambda"]);
/** Setup that cannot be removed or moved without changing behaviour. */
const UNMOVABLE_TYPES = new Set(["yield", "named_expression", "lambda"]);
const CALL_TYPES = new Set(["call", "await"]);
const ALLOCATION_TYPES = new Set([
  "list",
  "dictionary",
  "set",
  "list_comprehension",
  "set_comprehension",
  "dictionary_comprehension",
  "generator_expression",
]);
/** Empty built-in constructors (`set()`, `dict()`) allocate like a display does. */
const EMPTY_CONSTRUCTORS = new Set(["set", "list", "dict", "frozenset", "bytearray"]);
const EXIT_TYPES = new Set([
  "return_statement",
  "raise_statement",
  "continue_statement",
  "break_statement",
]);
const JUMP_TYPES = new Set(["continue_statement", "break_statement"]);
/** Guard exits on a normal path; a `raise` guard is error handling that rarely fires. */
const GUARD_EXIT_TYPES = new Set(["return_statement", ...JUMP_TYPES]);
/** Calls that cannot change their arguments, so a guard may read those safely. */
const PURE_FUNCTIONS = new Set([
  "sorted",
  "min",
  "max",
  "sum",
  "abs",
  "round",
  "any",
  "all",
  "list",
  "tuple",
  "dict",
  "set",
  "frozenset",
  "divmod",
  "hash",
  "ord",
  "chr",
  "format",
]);
const PURE_METHODS = new Set([
  "split",
  "rsplit",
  "splitlines",
  "strip",
  "lstrip",
  "rstrip",
  "lower",
  "upper",
  "casefold",
  "title",
  "replace",
  "partition",
  "rpartition",
  "encode",
  "decode",
  "format",
  "join",
  "startswith",
  "endswith",
  "find",
  "rfind",
  "count",
]);
/** Methods whose call is the point (state change): moving them below a guard changes behaviour. */
const MUTATORS = new Set([
  "pop",
  "popitem",
  "popleft",
  "append",
  "appendleft",
  "extend",
  "insert",
  "remove",
  "clear",
  "update",
  "setdefault",
  "add",
  "discard",
  "send",
  "write",
  "writelines",
  "seek",
  "close",
  "acquire",
  "release",
  "put",
]);

type Cost = "allocation" | "call" | "setup";

interface Init {
  stmt: Parser.SyntaxNode;
  name: string;
  value: Parser.SyntaxNode;
  cost: Cost;
  /** Fully qualified Tier A callee (`setup-cost.json`) when cost is `setup`. */
  factory?: string;
  signal?: SetupSignal;
}

interface FunctionInfo {
  node: Parser.SyntaxNode;
  /** Names captured by closures or declared `global` / `nonlocal`. */
  unsafe: Set<string>;
}

function statementsOf(block: Parser.SyntaxNode | null): Parser.SyntaxNode[] {
  return block ? block.namedChildren.filter((c) => c.type !== "comment") : [];
}

function contains(node: Parser.SyntaxNode, types: Set<string>): boolean {
  if (types.has(node.type)) return true;
  return node.namedChildren.some((c) => contains(c, types));
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

function nameUses(node: Parser.SyntaxNode | null, into: Parser.SyntaxNode[] = []): Parser.SyntaxNode[] {
  if (!node) return into;
  if (node.type === "identifier" && isNameUse(node)) into.push(node);
  for (const child of node.namedChildren) nameUses(child, into);
  return into;
}

function mentions(node: Parser.SyntaxNode | null, name: string): boolean {
  return nameUses(node).some((id) => id.text === name);
}

/** `name = value` (optionally annotated) as a whole statement; chained `a = b = c` excluded. */
function plainAssignment(stmt: Parser.SyntaxNode): { name: string; value: Parser.SyntaxNode } | null {
  if (stmt.type !== "expression_statement") return null;
  const inner = statementsOf(stmt);
  if (inner.length !== 1 || inner[0].type !== "assignment") return null;
  const left = inner[0].childForFieldName("left");
  const right = inner[0].childForFieldName("right");
  if (!left || left.type !== "identifier" || !right || right.type === "assignment") return null;
  return { name: left.text, value: right };
}

/** `name = <value not reading name>`: the statement replaces whatever `name` held. */
function overwrites(stmt: Parser.SyntaxNode, name: string): boolean {
  const a = plainAssignment(stmt);
  return a !== null && a.name === name && !mentions(a.value, name);
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

/** Calls in `node` other than empty built-in constructors and O(1) built-ins (`len`, `isinstance`). */
function realCalls(node: Parser.SyntaxNode): Parser.SyntaxNode[] {
  return [...node.descendantsOfType("call"), ...node.descendantsOfType("await")].filter(
    (c) =>
      c.type === "await" ||
      (!isEmptyConstructor(c) && !isTrivialBuiltinCall(c.childForFieldName("function")?.text ?? ""))
  );
}

/** Every call in the setup is a known pure built-in or string method. */
function onlyPureCalls(value: Parser.SyntaxNode): boolean {
  return realCalls(value).every((c) => {
    if (c.type !== "call") return false;
    const callee = c.childForFieldName("function");
    if (callee?.type === "identifier") return PURE_FUNCTIONS.has(callee.text);
    return callee?.type === "attribute" && PURE_METHODS.has(callee.childForFieldName("attribute")?.text ?? "");
  });
}

/**
 * Cost class of an initializer value, or null when it is free (constants, names,
 * lookups) or cannot be removed or moved safely (yield, walrus, volatile calls).
 */
function costOf(
  value: Parser.SyntaxNode,
  aliases: ReadonlyMap<string, string>
): { cost: Cost; factory?: string; signal?: SetupSignal } | null {
  if (contains(value, UNMOVABLE_TYPES)) return null;
  const calls = realCalls(value);
  for (const call of calls) {
    const callee = call.type === "call" ? call.childForFieldName("function")?.text ?? "" : "";
    if (callee && isVolatileCall(callee)) return null; // time, random, next(), read()…
  }
  for (const call of calls) {
    if (call.type !== "call") continue;
    const resolved = resolveCallee(call.childForFieldName("function")?.text ?? "", aliases);
    const signal = setupSignalFor(resolved);
    if (signal) return { cost: "setup", factory: resolved, signal };
  }
  if (calls.length > 0) return { cost: "call" };
  if (isEmptyConstructor(value) || contains(value, ALLOCATION_TYPES)) return { cost: "allocation" };
  return null;
}

/** Names the function cannot treat as private locals, or null to skip the whole function. */
function unsafeNames(fn: Parser.SyntaxNode): Set<string> | null {
  const names = new Set<string>();
  let frameAccess = false;
  const visit = (node: Parser.SyntaxNode) => {
    if (node.id !== fn.id && SCOPE_TYPES.has(node.type)) {
      // Captured by a closure: a call before the overwrite may read it.
      for (const id of nameUses(node)) names.add(id.text);
      return;
    }
    if (node.type === "global_statement" || node.type === "nonlocal_statement") {
      for (const id of nameUses(node)) names.add(id.text);
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

/** Inside a `try` or `with` (within the function), a handler or `__exit__` may still see the setup. */
function exceptionCanEscape(block: Parser.SyntaxNode, fn: Parser.SyntaxNode): boolean {
  for (let curr = block.parent; curr && curr.id !== fn.id; curr = curr.parent) {
    if (curr.type === "try_statement" || curr.type === "with_statement") return true;
  }
  return false;
}

function enclosingLoop(node: Parser.SyntaxNode, fn: Parser.SyntaxNode): "for" | "while" | undefined {
  for (let curr = node.parent; curr && curr.id !== fn.id; curr = curr.parent) {
    if (curr.type === "for_statement") return "for";
    if (curr.type === "while_statement") return "while";
  }
  return undefined;
}

/**
 * `continue` / `break` leave the setup alive for the next iteration or the code
 * after the loop. That is safe only when every mention of the name sits in the
 * setup's own block, after the setup, so it is re-run before any read.
 */
function jumpIsSafe(init: Init, block: Parser.SyntaxNode, fn: Parser.SyntaxNode): boolean {
  return nameUses(fn.childForFieldName("body"))
    .filter((id) => id.text === init.name)
    .every(
      (id) =>
        id.startIndex >= init.stmt.startIndex &&
        id.startIndex >= block.startIndex &&
        id.endIndex <= block.endIndex
    );
}

/** Statements that leave the block, including a top-level jump. */
function exitType(stmts: Parser.SyntaxNode[]): string | null {
  const last = stmts[stmts.length - 1];
  return last && EXIT_TYPES.has(last.type) ? last.type : null;
}

/** `if cond: …; return|continue|break` with no `elif` / `else` and no walrus. */
function isGuard(stmt: Parser.SyntaxNode): boolean {
  if (stmt.type !== "if_statement") return false;
  if (stmt.childrenForFieldName("alternative").length > 0) return false;
  if (contains(stmt, new Set(["named_expression"]))) return false;
  const exit = exitType(statementsOf(stmt.childForFieldName("consequence")));
  return exit !== null && GUARD_EXIT_TYPES.has(exit);
}

/** The setup calls a state-changing method (`cache.pop(k)`, `kwargs.pop("x")`). */
function callsMutator(value: Parser.SyntaxNode): boolean {
  const names = [value, ...value.descendantsOfType("call")]
    .filter((c) => c.type === "call")
    .map((c) => c.childForFieldName("function"))
    .filter((f) => f?.type === "attribute")
    .map((f) => f!.childForFieldName("attribute")?.text ?? "");
  // `_map(kwds.pop, …)` passes a mutator without calling it here.
  const passed = value.descendantsOfType("attribute").map((a) => a.childForFieldName("attribute")?.text ?? "");
  return [...names, ...passed].some((n) => MUTATORS.has(n));
}

/** Arm bodies of an `if` chain, or null when it has no `else` (falling through keeps the setup). */
function armBodies(ifNode: Parser.SyntaxNode): Parser.SyntaxNode[] | null {
  const alts = ifNode.childrenForFieldName("alternative");
  if (alts.length === 0 || alts[alts.length - 1].type !== "else_clause") return null;
  const bodies = [ifNode.childForFieldName("consequence")];
  for (const alt of alts) {
    bodies.push(alt.childForFieldName(alt.type === "elif_clause" ? "consequence" : "body"));
  }
  return bodies.every((b) => b !== null) ? (bodies as Parser.SyntaxNode[]) : null;
}

/**
 * How an arm treats the setup: `overwrite` (assigns the name before reading it),
 * `exit` (leaves without reading it, returning the exit type) or null (reads it
 * or falls through).
 */
function armOutcome(body: Parser.SyntaxNode, name: string): { overwrite: true } | { exit: string } | null {
  for (const stmt of statementsOf(body)) {
    if (overwrites(stmt, name)) return { overwrite: true };
    if (mentions(stmt, name)) return null;
    if (EXIT_TYPES.has(stmt.type)) return { exit: stmt.type };
  }
  return null;
}

function costPhrase(init: Init): string {
  if (init.cost === "setup") {
    const what = { compile: "compiles a pattern", connection: "opens a connection", "file-open": "opens a file" };
    return `${what[init.signal!]} (\`${init.factory}\`)`;
  }
  if (init.cost === "call") return "runs a call";
  return "allocates a new object";
}

interface Waste {
  kind: string;
  init: Init;
  /** The statement that makes the setup unnecessary (the `if` chain or first guard). */
  site: Parser.SyntaxNode;
  /** Lines where a `# noqa` suppresses the finding. */
  rows: number[];
  why: string;
  fix: string;
}

function analyseBlock(
  block: Parser.SyntaxNode,
  fn: FunctionInfo,
  aliases: ReadonlyMap<string, string>
): Waste[] {
  if (exceptionCanEscape(block, fn.node)) return [];
  const stmts = statementsOf(block);
  const found: Waste[] = [];

  stmts.forEach((stmt, i) => {
    const a = plainAssignment(stmt);
    if (!a || a.name === "_" || fn.unsafe.has(a.name) || mentions(a.value, a.name)) return;
    const cost = costOf(a.value, aliases);
    if (!cost) return;
    const init: Init = { stmt, name: a.name, value: a.value, ...cost };
    const reads = new Set(nameUses(a.value).map((id) => id.text));
    const guards: Parser.SyntaxNode[] = [];
    // A setup that runs code may change objects it reads (`do.decompress()` then
    // `if not do.eof`), so only an allocation or pure call may move past a guard that reads them.
    let movable = !callsMutator(init.value);
    const pure = init.cost === "allocation" || onlyPureCalls(init.value);

    for (const later of stmts.slice(i + 1)) {
      if (!mentions(later, init.name)) {
        const touchesReads = nameUses(later).some((id) => reads.has(id.text));
        if (movable && isGuard(later) && (pure || !touchesReads)) {
          guards.push(later);
        } else if (touchesReads || (!pure && contains(later, CALL_TYPES))) {
          // An impure setup must not swap order with another call's side effects.
          movable = false;
        }
        continue;
      }

      const overwritten = overwrittenOnEveryArm(later, init, block, fn.node);
      if (overwritten) {
        found.push(overwritten);
      } else if (guards.length > 0 && !overwrites(later, init.name)) {
        // A straight-line overwrite after the guard is C1.2's dead store.
        const exits = guards.map((g) => exitType(statementsOf(g.childForFieldName("consequence")))!);
        if (exits.some((e) => JUMP_TYPES.has(e)) && !jumpIsSafe(init, block, fn.node)) break;
        const guard = guards[0];
        const cond = normalized(guard.childForFieldName("condition")!);
        found.push({
          kind: EARLY_EXIT_KIND,
          init,
          site: guard,
          rows: [stmt.startPosition.row, guard.startPosition.row],
          why: `\`${init.name} = ${normalized(init.value)}\` ${costPhrase(init)} before the guard \`if ${cond}:\`${
            guards.length > 1 ? ` (and ${guards.length - 1} more)` : ""
          }, which leaves without using it, so the setup is wasted every time the guard fires`,
          fix: `move \`${init.name} = ${normalized(init.value)}\` below the guard${
            guards.length > 1 ? "s" : ""
          } so it runs only on the path that reads \`${init.name}\``,
        });
      }
      break;
    }
  });
  return found;
}

/** `x = setup; if a: x = … else: x = …`: every arm replaces or abandons the setup. */
function overwrittenOnEveryArm(
  stmt: Parser.SyntaxNode,
  init: Init,
  block: Parser.SyntaxNode,
  fn: Parser.SyntaxNode
): Waste | null {
  if (stmt.type !== "if_statement" || mentions(stmt.childForFieldName("condition"), init.name)) {
    return null;
  }
  for (const alt of stmt.childrenForFieldName("alternative")) {
    if (mentions(alt.childForFieldName("condition"), init.name)) return null;
  }
  const bodies = armBodies(stmt);
  if (!bodies) return null;
  const outcomes = bodies.map((b) => armOutcome(b, init.name));
  if (outcomes.some((o) => o === null)) return null;
  if (!outcomes.some((o) => o && "overwrite" in o)) return null;
  const jumps = outcomes.some((o) => o && "exit" in o && JUMP_TYPES.has(o.exit));
  if (jumps && !jumpIsSafe(init, block, fn)) return null;

  const cond = normalized(stmt.childForFieldName("condition")!);
  return {
    kind: OVERWRITTEN_KIND,
    init,
    site: stmt,
    rows: [init.stmt.startPosition.row, stmt.startPosition.row],
    why: `\`${init.name} = ${normalized(init.value)}\` ${costPhrase(init)}, but every arm of \`if ${cond}:\` ${
      outcomes.some((o) => o && "exit" in o) ? "assigns it again or leaves" : "assigns it again"
    } before reading it, so the initial value is never used`,
    fix: `delete the initial \`${init.name} = ${normalized(init.value)}\`; every arm of \`if ${cond}:\` already assigns \`${init.name}\``,
  };
}

function suppressed(sourceLines: string[], rows: number[]): boolean {
  return rows.some((r) => isLineSuppressed(sourceLines[r] ?? "", [CHECK]).isSuppressed);
}

function ordinals() {
  const seen = new Map<string, number>();
  return (key: string) => {
    const n = seen.get(key) ?? 0;
    seen.set(key, n + 1);
    return n;
  };
}

/**
 * Pure function detecting setup whose result is unused on some paths (CODE-C1.6):
 * a costly function-local initializer that every `if`/`elif`/`else` arm overwrites
 * (S1), or that runs before a guard which exits without using it (S2).
 * Never performs I/O or executes code.
 */
export function detectUnnecessaryInitialization(
  rootNode: Parser.SyntaxNode,
  filePath: string,
  sourceLines: string[]
): Finding[] {
  const findings: Finding[] = [];
  const next = ordinals();
  const aliases = collectImportAliases(rootNode);

  const report = (w: Waste, fn: Parser.SyntaxNode) => {
    if (suppressed(sourceLines, w.rows)) return;
    const { init } = w;
    const runsCode = init.cost !== "allocation";
    const severity: Severity = init.cost === "setup" ? "high" : init.cost === "call" ? "medium" : "low";
    const confidence: Confidence = runsCode ? "medium" : "high";
    const caveat = w.kind === OVERWRITTEN_KIND ? KEEP_THE_CALL : CALL_ORDER;
    const limitations = runsCode ? [PATH_SHARE, caveat] : [PATH_SHARE];

    const qualname = getEnclosingQualname(init.stmt);
    const key = `${qualname}:${init.name}:${normalized(init.stmt)}`;
    const ordinal = next(`${w.kind}:${key}`);
    const startLine = init.stmt.startPosition.row + 1;
    const loopType = enclosingLoop(init.stmt, fn);

    findings.push({
      check: CHECK,
      kind: w.kind,
      fingerprint: generateFingerprint(CHECK, w.kind, filePath, `${key}:${ordinal}`),
      identity: `${w.kind}:${key}:${ordinal}`,
      location: { path: filePath, startLine, endLine: w.site.endPosition.row + 1 },
      evidence: {
        snippet: (sourceLines[init.stmt.startPosition.row] ?? "").trim(),
        symbol: init.name,
        expr: normalized(init.value),
        ...(init.factory ? { factory: init.factory } : {}),
        ...(loopType ? { loopType } : {}),
      },
      why: `${w.why}${loopType ? `; this runs inside the enclosing ${loopType} loop` : ""}.`,
      severity,
      confidence,
      limitations,
      evidenceTier: "static",
      impact: {
        quantified: false,
        reason:
          "Saved work = setup cost × how often the path that discards it runs; neither is measured statically.",
      },
      references: REFERENCES.map((ref) => ({ ...ref })),
      agentPrompt: `In ${filePath}:${startLine}, ${w.fix}${
        runsCode && w.kind === OVERWRITTEN_KIND
          ? `; keep \`${normalized(init.value)}\` as a bare statement only if its side effects are needed`
          : ""
      }. Static finding only — keep the change minimal and covered by tests.`,
      detector: { id: "owner-a-static-scan", version: DETECTOR_VERSION },
    });
  };

  for (const fn of rootNode.descendantsOfType("function_definition")) {
    const unsafe = unsafeNames(fn);
    if (!unsafe) continue;
    for (const block of ownBlocks(fn)) {
      for (const w of analyseBlock(block, { node: fn, unsafe }, aliases)) report(w, fn);
    }
  }
  return findings.sort((a, b) => a.location.startLine - b.location.startLine);
}
