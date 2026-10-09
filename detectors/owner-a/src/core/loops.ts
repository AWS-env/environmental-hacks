import Parser from "tree-sitter";
import setupCostData from "./setup-cost.json" with { type: "json" };

export type LoopType = "for" | "while";

export interface LoopInfo {
  loopNode: Parser.SyntaxNode;
  loopType: LoopType;
  headerText: string;
  startLine: number;
  endLine: number;
  bodyNode: Parser.SyntaxNode | null;
  /** All names assigned or bound anywhere in the loop body or loop target */
  assignedNames: Set<string>;
  /** Names modified in place (attribute/subscript stores or method invocations) */
  touchedBases: Set<string>;
  /** Map of identifier name to list of AST call nodes in the loop body where it is passed as argument */
  callArguments: Map<string, Parser.SyntaxNode[]>;
  /** True if the loop body unconditionally breaks, returns, or raises (runs at most once) */
  runsAtMostOnce: boolean;
  /** Enclosing function or class qualname (e.g. 'MyClass.method' or '<module>') */
  enclosingQualname: string;
  /** Variables declared global or nonlocal in the enclosing scope */
  globalOrNonlocalNames: Set<string>;
  /** Enclosing parent loop if nested */
  parentLoop: LoopInfo | null;
}

/**
 * Known volatile, non-pure, or stateful calls whose hoisting would alter program semantics.
 * Hoisting these can break random seeds, time readings, I/O streams, or generator iterations.
 */
export const VOLATILE_CALLS = new Set<string>([
  "now",
  "utcnow",
  "time",
  "monotonic",
  "perf_counter",
  "process_time",
  "sleep",
  "random",
  "randint",
  "choice",
  "choices",
  "sample",
  "uniform",
  "randrange",
  "getrandbits",
  "uuid1",
  "uuid4",
  "input",
  "print",
  "exec",
  "eval",
  "read",
  "readline",
  "readlines",
  "next",
  "recv",
  "recvfrom",
  "poll",
  "select",
  "urandom",
  "get_random_bytes",
]);

export type SetupSignal = "compile" | "connection" | "file-open";

/**
 * Heavy (Tier A) setup callees owned by C3.3 (#62, per-iteration setup), keyed by
 * fully qualified name. Data lives in `setup-cost.json` so C3.2 and C3.3 share one
 * source of truth for "this call belongs to C3.3".
 */
export const SETUP_CALLEES: ReadonlyMap<string, SetupSignal> = new Map(
  Object.entries(setupCostData.callees as Record<string, SetupSignal>)
);

/** Kept for compatibility: the Tier A callee names. */
export const C33_SETUP_CALLEES = new Set<string>(SETUP_CALLEES.keys());

/**
 * Map local names to fully qualified import targets for the file:
 * `import requests as rq` → rq: requests; `from requests import Session as S`
 * → S: requests.Session. Relative and star imports are ignored.
 */
export function collectImportAliases(
  rootNode: Parser.SyntaxNode
): Map<string, string> {
  const aliases = new Map<string, string>();
  const visit = (node: Parser.SyntaxNode) => {
    if (node.type === "import_statement") {
      for (const child of node.namedChildren) {
        if (child.type === "dotted_name") {
          const top = child.text.split(".")[0];
          aliases.set(top, top);
        } else if (child.type === "aliased_import") {
          const name = child.childForFieldName("name")?.text;
          const alias = child.childForFieldName("alias")?.text;
          if (name && alias) aliases.set(alias, name);
        }
      }
    } else if (node.type === "import_from_statement") {
      const module = node.childForFieldName("module_name");
      if (!module || module.type === "relative_import") return;
      for (const child of node.namedChildren) {
        if (child.startIndex === module.startIndex) continue;
        if (child.type === "dotted_name") {
          aliases.set(child.text, `${module.text}.${child.text}`);
        } else if (child.type === "aliased_import") {
          const name = child.childForFieldName("name")?.text;
          const alias = child.childForFieldName("alias")?.text;
          if (name && alias) aliases.set(alias, `${module.text}.${name}`);
        }
      }
    }
    for (const child of node.namedChildren) visit(child);
  };
  visit(rootNode);
  return aliases;
}

/** Resolve the head of a dotted callee through the file's import aliases. */
export function resolveCallee(
  calleeText: string,
  aliases?: ReadonlyMap<string, string>
): string {
  const normalized = calleeText.replace(/\s+/g, "");
  if (!aliases) return normalized;
  const [head, ...rest] = normalized.split(".");
  const target = aliases.get(head);
  if (!target) return normalized;
  return [target, ...rest].join(".");
}

/** Tier A signal for a (resolved) callee, if any. */
export function setupSignalFor(resolvedCallee: string): SetupSignal | null {
  const direct = SETUP_CALLEES.get(resolvedCallee);
  if (direct) return direct;
  const last = resolvedCallee.split(".").pop() ?? "";
  // `<module>.compile(...)` / `<module>.open(...)` from unlisted modules.
  if (last === "compile") return "compile";
  if (last === "open" && resolvedCallee !== last) return "file-open";
  return null;
}

/**
 * Trivial O(1) built-ins that fall outside the "costly value" caveat and overlap with C3.1.
 */
export const TRIVIAL_BUILTIN_CALLS = new Set<string>([
  "len",
  "isinstance",
  "issubclass",
  "type",
  "callable",
  "id",
  "repr",
  "str",
  "int",
  "float",
  "bool",
]);

/**
 * Check if a callee name matches known volatile / non-pure functions.
 */
export function isVolatileCall(calleeText: string): boolean {
  const normalized = calleeText.trim();
  if (VOLATILE_CALLS.has(normalized)) {
    return true;
  }
  const parts = normalized.split(".");
  const last = parts[parts.length - 1];
  if (last && VOLATILE_CALLS.has(last)) {
    return true;
  }
  return false;
}

/**
 * Check if a callee matches C3.3 heavy setup constructors (including CapWords class names).
 */
export function isC33SetupCallee(
  calleeText: string,
  aliases?: ReadonlyMap<string, string>
): boolean {
  const resolved = resolveCallee(calleeText, aliases);
  if (setupSignalFor(resolved) !== null) {
    return true;
  }
  const parts = resolved.split(".");
  const last = parts[parts.length - 1];
  // Check for CapWords class construction (e.g. `MyClass(...)`, `models.User(...)`)
  if (last && /^[A-Z][a-zA-Z0-9]+$/.test(last)) {
    return true;
  }
  return false;
}

/**
 * Check if a callee matches trivial O(1) built-ins like `len`.
 */
export function isTrivialBuiltinCall(calleeText: string): boolean {
  const normalized = calleeText.trim();
  return TRIVIAL_BUILTIN_CALLS.has(normalized);
}

/**
 * Extract all identifiers under an AST subtree.
 */
export function collectAllIdentifiers(node: Parser.SyntaxNode | null): string[] {
  if (!node) return [];
  const results: string[] = [];
  function walk(n: Parser.SyntaxNode) {
    if (n.type === "identifier") {
      results.push(n.text);
    }
    for (const child of n.children) {
      walk(child);
    }
  }
  walk(node);
  return results;
}

/**
 * Extract the root base identifier of an attribute or subscript chain.
 * E.g. `settings.limits["max"]` -> `settings`
 */
export function getRootIdentifier(node: Parser.SyntaxNode | null): string | null {
  if (!node) return null;
  let curr: Parser.SyntaxNode = node;
  while (curr) {
    if (curr.type === "identifier") {
      return curr.text;
    }
    if (curr.type === "attribute") {
      const obj = curr.childForFieldName("object");
      if (obj) {
        curr = obj;
        continue;
      }
    }
    if (curr.type === "subscript") {
      const val = curr.childForFieldName("value");
      if (val) {
        curr = val;
        continue;
      }
    }
    // If we reach another construct, try first named child
    if (curr.namedChildCount > 0) {
      curr = curr.namedChild(0)!;
    } else {
      break;
    }
  }
  return null;
}

/**
 * Extract the enclosing qualname (e.g. `MyClass.process` or `<module>`).
 */
export function getEnclosingQualname(node: Parser.SyntaxNode): string {
  const parts: string[] = [];
  let curr: Parser.SyntaxNode | null = node.parent;

  while (curr) {
    if (curr.type === "function_definition") {
      const nameNode = curr.childForFieldName("name");
      if (nameNode) {
        parts.unshift(nameNode.text);
      }
    } else if (curr.type === "class_definition") {
      const nameNode = curr.childForFieldName("name");
      if (nameNode) {
        parts.unshift(nameNode.text);
      }
    }
    curr = curr.parent;
  }

  return parts.length > 0 ? parts.join(".") : "<module>";
}

/**
 * Collect all identifiers declared global or nonlocal in the enclosing function scope.
 */
function collectGlobalOrNonlocal(node: Parser.SyntaxNode): Set<string> {
  const result = new Set<string>();
  let scopeRoot: Parser.SyntaxNode | null = node.parent;

  while (scopeRoot && scopeRoot.type !== "function_definition" && scopeRoot.type !== "module") {
    scopeRoot = scopeRoot.parent;
  }

  if (!scopeRoot) return result;

  function walk(n: Parser.SyntaxNode) {
    if (n.type === "global_statement" || n.type === "nonlocal_statement") {
      for (const id of collectAllIdentifiers(n)) {
        result.add(id);
      }
    }
    // Don't descend into nested functions
    if (n.id !== scopeRoot?.id && (n.type === "function_definition" || n.type === "class_definition")) {
      return;
    }
    for (const child of n.children) {
      walk(child);
    }
  }

  walk(scopeRoot);
  return result;
}

/**
 * Determine if a loop body runs at most once (e.g. ends with unconditional break / return / raise).
 */
export function checkRunsAtMostOnce(bodyNode: Parser.SyntaxNode | null): boolean {
  if (!bodyNode) return false;

  // The body is typically a block
  const block = bodyNode.type === "block" ? bodyNode : bodyNode;
  const namedChildren = block.namedChildren;
  if (namedChildren.length === 0) return false;

  // Check top-level statements of the loop body
  for (const child of namedChildren) {
    if (
      child.type === "break_statement" ||
      child.type === "return_statement" ||
      child.type === "raise_statement"
    ) {
      return true;
    }
  }

  return false;
}

const NESTED_SCOPES = new Set(["function_definition", "class_definition", "lambda"]);
const LOOP_TYPES = new Set(["for_statement", "while_statement"]);

/**
 * True if the loop body can leave this loop before the iterable is exhausted:
 * a `break` owned by this loop (not by a nested loop), or a `return` anywhere in
 * the body (it also leaves enclosing loops). A `raise` counts only outside a
 * `try` body, where a local handler could swallow it. Nested defs are skipped.
 */
export function loopExitsEarly(loopNode: Parser.SyntaxNode): boolean {
  const body = loopNode.childForFieldName("body");
  if (!body) return false;
  function walk(n: Parser.SyntaxNode, inNestedLoop: boolean, inTry: boolean): boolean {
    if (NESTED_SCOPES.has(n.type)) return false;
    if (n.type === "break_statement") return !inNestedLoop;
    if (n.type === "return_statement") return true;
    if (n.type === "raise_statement") return !inTry;
    const nestedLoop = inNestedLoop || LOOP_TYPES.has(n.type);
    for (const child of n.namedChildren) {
      const tryBody = n.type === "try_statement" && child === n.childForFieldName("body");
      if (walk(child, nestedLoop, inTry || tryBody)) return true;
    }
    return false;
  }
  return body.namedChildren.some((c) => walk(c, false, false));
}

/**
 * Collect all names assigned or mutated inside a loop node.
 */
function analyzeLoopBody(
  loopNode: Parser.SyntaxNode,
  loopType: LoopType,
  bodyNode: Parser.SyntaxNode | null
): {
  assignedNames: Set<string>;
  touchedBases: Set<string>;
  callArguments: Map<string, Parser.SyntaxNode[]>;
} {
  const assignedNames = new Set<string>();
  const touchedBases = new Set<string>();
  const callArguments = new Map<string, Parser.SyntaxNode[]>();

  // 1. For loop target variable(s)
  if (loopType === "for") {
    const leftNode = loopNode.childForFieldName("left");
    if (leftNode) {
      for (const id of collectAllIdentifiers(leftNode)) {
        assignedNames.add(id);
      }
    }
  }

  if (!bodyNode) {
    return { assignedNames, touchedBases, callArguments };
  }

  // 2. Walk body statements (do not descend into nested function or class definitions)
  function walk(n: Parser.SyntaxNode) {
    if (n.id !== bodyNode?.id && (n.type === "function_definition" || n.type === "class_definition")) {
      return;
    }

    // Assignment LHS
    if (n.type === "assignment" || n.type === "augmented_assignment") {
      const left = n.childForFieldName("left");
      if (left) {
        if (left.type === "identifier") {
          assignedNames.add(left.text);
        } else if (left.type === "attribute" || left.type === "subscript") {
          const root = getRootIdentifier(left);
          if (root) {
            touchedBases.add(root);
          }
        } else {
          // Unpacking target (tuples, lists)
          for (const id of collectAllIdentifiers(left)) {
            assignedNames.add(id);
          }
        }
      }
    }

    // Walrus operator (named_expression)
    if (n.type === "named_expression") {
      const name = n.childForFieldName("name");
      if (name) {
        assignedNames.add(name.text);
      }
    }

    // With statement alias
    if (n.type === "as_pattern") {
      const alias = n.childForFieldName("alias") || n.lastNamedChild;
      if (alias) {
        for (const id of collectAllIdentifiers(alias)) {
          assignedNames.add(id);
        }
      }
    }

    // Except clause alias
    if (n.type === "except_clause") {
      const asPattern = n.namedChildren.find(
        (c) => c.type === "as_pattern" || c.type === "identifier"
      );
      if (asPattern) {
        for (const id of collectAllIdentifiers(asPattern)) {
          assignedNames.add(id);
        }
      }
    }

    // Nested for statement loop target
    if (n.id !== loopNode.id && n.type === "for_statement") {
      const target = n.childForFieldName("left");
      if (target) {
        for (const id of collectAllIdentifiers(target)) {
          assignedNames.add(id);
        }
      }
    }

    // Method calls: `obj.method(...)` -> `obj` is touched
    if (n.type === "call") {
      const func = n.childForFieldName("function");
      if (func && func.type === "attribute") {
        const root = getRootIdentifier(func);
        if (root) {
          touchedBases.add(root);
        }
      }

      // Collect arguments passed to call
      const args = n.childForFieldName("arguments");
      if (args) {
        for (const id of collectAllIdentifiers(args)) {
          const list = callArguments.get(id) ?? [];
          list.push(n);
          callArguments.set(id, list);
        }
      }
    }

    for (const child of n.children) {
      walk(child);
    }
  }

  walk(bodyNode);

  return { assignedNames, touchedBases, callArguments };
}

/**
 * Traverse an AST and collect all `for` and `while` loops with full scope and mutation analysis.
 */
export function collectLoops(
  rootNode: Parser.SyntaxNode,
  sourceLines: string[]
): LoopInfo[] {
  const loops: LoopInfo[] = [];

  function visit(node: Parser.SyntaxNode, parentLoop: LoopInfo | null) {
    let currentLoop: LoopInfo | null = null;

    if (node.type === "for_statement" || node.type === "while_statement") {
      const loopType: LoopType = node.type === "for_statement" ? "for" : "while";
      const startLine = node.startPosition.row + 1;
      const endLine = node.endPosition.row + 1;
      const bodyNode = node.childForFieldName("body");

      // Extract header line (strip trailing colon and trim)
      const headerRaw = sourceLines[node.startPosition.row]?.trim() || node.text.split("\n")[0];
      const headerText = headerRaw.replace(/:$/, "").trim();

      const { assignedNames, touchedBases, callArguments } = analyzeLoopBody(
        node,
        loopType,
        bodyNode
      );

      const runsAtMostOnce = checkRunsAtMostOnce(bodyNode);
      const enclosingQualname = getEnclosingQualname(node);
      const globalOrNonlocalNames = collectGlobalOrNonlocal(node);

      currentLoop = {
        loopNode: node,
        loopType,
        headerText,
        startLine,
        endLine,
        bodyNode,
        assignedNames,
        touchedBases,
        callArguments,
        runsAtMostOnce,
        enclosingQualname,
        globalOrNonlocalNames,
        parentLoop,
      };

      loops.push(currentLoop);
    }

    const nextParent = currentLoop ?? parentLoop;
    for (const child of node.namedChildren) {
      visit(child, nextParent);
    }
  }

  visit(rootNode, null);
  return loops;
}

/** True for a bare name or a pure attribute chain (`items`, `self.items`, `a.b.c`). */
export function isNameChain(node: Parser.SyntaxNode | null): boolean {
  if (!node) return false;
  if (node.type === "identifier") return true;
  if (node.type === "attribute") return isNameChain(node.childForFieldName("object"));
  return false;
}

/** Exact base-chain comparison (`self.items` ≠ `other.items` ≠ `items`), ignoring whitespace. */
export function sameBaseChain(a: Parser.SyntaxNode | null, b: Parser.SyntaxNode | null): boolean {
  if (!isNameChain(a) || !isNameChain(b)) return false;
  return a!.text.replace(/\s+/g, "") === b!.text.replace(/\s+/g, "");
}

/**
 * The collection a `for` loop iterates directly: `for x in items`, `for k in d.keys()`
 * (also `.values()` / `.items()`), or `for i, x in enumerate(items)`. Anything else —
 * including explicit copies like `items[:]`, `list(items)`, `sorted(items)` — returns null.
 */
export function iteratedCollection(forNode: Parser.SyntaxNode): Parser.SyntaxNode | null {
  let iter = forNode.childForFieldName("right");
  while (iter?.type === "parenthesized_expression") iter = iter.namedChildren[0] ?? null;
  if (!iter) return null;
  if (iter.type === "call") {
    const fn = iter.childForFieldName("function");
    const args = iter.childForFieldName("arguments");
    const named = args?.namedChildren.filter((c) => c.type !== "comment") ?? [];
    if (fn?.type === "attribute" && named.length === 0) {
      const method = fn.childForFieldName("attribute")?.text;
      if (method === "keys" || method === "values" || method === "items") {
        const obj = fn.childForFieldName("object");
        return isNameChain(obj) ? obj : null;
      }
    }
    if (fn?.type === "identifier" && fn.text === "enumerate" && named.length >= 1) {
      return isNameChain(named[0]) ? named[0] : null;
    }
    return null;
  }
  return isNameChain(iter) ? iter : null;
}
