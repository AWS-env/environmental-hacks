import Parser from "tree-sitter";

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

/**
 * Heavy setup constructors owned by C3.3 (#62, per-iteration setup).
 * C3.2 yields precedence to C3.3 on these callees so both do not fire.
 */
export const C33_SETUP_CALLEES = new Set<string>([
  "re.compile",
  "compile",
  "open",
  "Path.open",
  "pathlib.Path.open",
  "sqlite3.connect",
  "requests.Session",
  "urllib3.PoolManager",
  "boto3.client",
  "boto3.resource",
  "psycopg2.connect",
  "pymongo.MongoClient",
]);

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
export function isC33SetupCallee(calleeText: string): boolean {
  const normalized = calleeText.trim();
  if (C33_SETUP_CALLEES.has(normalized)) {
    return true;
  }
  const parts = normalized.split(".");
  const last = parts[parts.length - 1];
  if (last && C33_SETUP_CALLEES.has(last)) {
    return true;
  }
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
    if (n !== scopeRoot && (n.type === "function_definition" || n.type === "class_definition")) {
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
    if (n !== bodyNode && (n.type === "function_definition" || n.type === "class_definition")) {
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
    if (n !== loopNode && n.type === "for_statement") {
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
