import Parser from "tree-sitter";
import { Finding, generateFingerprint, Severity, Confidence } from "../../core/finding.js";
import { isLineSuppressed } from "../../core/suppressions.js";
import {
  collectLoops,
  LoopInfo,
  isVolatileCall,
  isC33SetupCallee,
  isTrivialBuiltinCall,
  collectAllIdentifiers,
  getRootIdentifier,
  collectImportAliases,
} from "../../core/loops.js";

const ARITHMETIC_OPERATORS = new Set([
  "+",
  "-",
  "*",
  "/",
  "//",
  "%",
  "**",
  "@",
  "&",
  "|",
  "^",
  "<<",
  ">>",
]);

interface InvariantCandidate {
  node: Parser.SyntaxNode;
  signalType: "call" | "chain" | "arithmetic";
  symbol: string;
  expr: string;
  severity: Severity;
  confidence: Confidence;
  why: string;
  limitations: string[];
}

/**
 * Check if an AST node is located inside any kind of comprehension or generator expression.
 */
function isInsideComprehension(node: Parser.SyntaxNode, loopBody: Parser.SyntaxNode): boolean {
  if (node.id === loopBody.id) return false;
  let curr: Parser.SyntaxNode | null = node.parent;
  while (curr && curr.id !== loopBody.id) {
    if (
      curr.type === "list_comprehension" ||
      curr.type === "dictionary_comprehension" ||
      curr.type === "set_comprehension" ||
      curr.type === "generator_expression"
    ) {
      return true;
    }
    curr = curr.parent;
  }
  return false;
}

/**
 * Check if an AST node is inside a nested function or class definition within the loop body.
 */
function isInsideNestedDef(node: Parser.SyntaxNode, loopBody: Parser.SyntaxNode): boolean {
  if (node.id === loopBody.id) return false;
  let curr: Parser.SyntaxNode | null = node.parent;
  while (curr && curr.id !== loopBody.id) {
    if (curr.type === "function_definition" || curr.type === "class_definition") {
      return true;
    }
    curr = curr.parent;
  }
  return false;
}

/**
 * Check if node `child` is a descendant of node `ancestor`.
 */
function isDescendantOf(child: Parser.SyntaxNode, ancestor: Parser.SyntaxNode): boolean {
  let curr: Parser.SyntaxNode | null = child.parent;
  while (curr) {
    if (curr.id === ancestor.id) {
      return true;
    }
    curr = curr.parent;
  }
  return false;
}

/**
 * Check if an AST node is in callee position of a call (e.g. `out.append` in `out.append(...)`).
 */
function isInCalleePosition(node: Parser.SyntaxNode): boolean {
  const parent = node.parent;
  if (!parent) return false;
  if (parent.type === "call") {
    const func = parent.childForFieldName("function");
    if (func?.id === node.id) return true;
  }
  return false;
}

/**
 * Check if an AST node is on the LHS of an assignment / mutation.
 */
function isInAssignmentTargetPosition(node: Parser.SyntaxNode): boolean {
  const parent = node.parent;
  if (!parent) return false;
  if (parent.type === "assignment" || parent.type === "augmented_assignment") {
    const left = parent.childForFieldName("left");
    if (left && (left.id === node.id || isDescendantOf(node, left))) return true;
  }
  return false;
}

/**
 * The expression sits in a `return`, or in a `raise` that no `try` inside the loop body can catch:
 * control leaves the loop there, so it is evaluated at most once per loop, not once per iteration.
 */
function isInLoopExitStatement(node: Parser.SyntaxNode, loopBody: Parser.SyntaxNode): boolean {
  let exit: "return" | "raise" | null = null;
  for (let curr: Parser.SyntaxNode | null = node; curr && curr.id !== loopBody.id; curr = curr.parent) {
    if (curr.type === "return_statement") exit = "return";
    else if (curr.type === "raise_statement") exit = "raise";
    else if (exit === "raise" && curr.type === "try_statement") exit = null; // may be caught: loop can continue
  }
  return exit !== null;
}

/** Statement-level calls that cannot change program state the invariant expression could depend on. */
const STATE_NEUTRAL_STATEMENT_CALLS = new Set(["print"]);

/**
 * The loop body makes a zero-argument bare-name statement-level call (`reset_mocks()`, `refresh()`).
 * A function that takes nothing can only act through closure or global state, which is invisible
 * here, so an expression that looks invariant may not be. Calls with arguments are not treated this
 * way (nearly every loop has one, and the arguments show what they touch). Returns the callee name, or null.
 */
function opaqueStatementCall(loopBody: Parser.SyntaxNode): string | null {
  let found: string | null = null;
  const visit = (n: Parser.SyntaxNode) => {
    if (found || n.type === "function_definition" || n.type === "class_definition" || n.type === "lambda") return;
    if (n.type === "expression_statement") {
      let expr: Parser.SyntaxNode | null = n.namedChildren[0] ?? null;
      if (expr?.type === "await") expr = expr.namedChildren[0] ?? null;
      const fn = expr?.type === "call" ? expr.childForFieldName("function") : null;
      const noArgs = (expr?.childForFieldName("arguments")?.namedChildren.length ?? 0) === 0;
      if (fn?.type === "identifier" && noArgs && !STATE_NEUTRAL_STATEMENT_CALLS.has(fn.text)) {
        found = fn.text;
        return;
      }
    }
    for (const c of n.namedChildren) visit(c);
  };
  visit(loopBody);
  return found;
}

/** Number of attribute/subscript hops from the root object (`a.b["c"]` → 2). */
function chainDepth(node: Parser.SyntaxNode): number {
  let depth = 0;
  let curr: Parser.SyntaxNode | null = node;
  while (curr && (curr.type === "attribute" || curr.type === "subscript")) {
    depth++;
    curr =
      curr.type === "attribute"
        ? curr.childForFieldName("object")
        : curr.childForFieldName("value");
  }
  return depth;
}

/** `with <node>:` / `with <node> as x:` (also async): entering and leaving the manager is per-iteration work. */
function isWithItemExpression(node: Parser.SyntaxNode): boolean {
  const parent = node.parent;
  if (!parent) return false;
  if (parent.type === "with_item") return true;
  return parent.type === "as_pattern" && parent.parent?.type === "with_item";
}

/**
 * Check if candidate expression node is invariant with respect to the given loop.
 */
function checkCandidateInvariance(
  node: Parser.SyntaxNode,
  loop: LoopInfo,
  aliases: ReadonlyMap<string, string>
): InvariantCandidate | null {
  if (isInCalleePosition(node)) {
    return null;
  }

  // Evaluated at most once per loop (control leaves the loop there): not a per-iteration recomputation.
  if (loop.bodyNode && isInLoopExitStatement(node, loop.bodyNode)) {
    return null;
  }

  if (isInAssignmentTargetPosition(node)) {
    return null;
  }

  if (node.type === "call") {
    const func = node.childForFieldName("function");
    if (!func) return null;

    // Guard: a call whose result is discarded (`print(header)`, `notify(cfg)`)
    // runs for its side effects, and an awaited call is I/O — hoisting either
    // changes behaviour. Only value-position calls are hoist candidates.
    const parentType = node.parent?.type;
    if (parentType === "expression_statement" || parentType === "await") {
      return null;
    }

    // Guard: a context manager has enter/exit effects that belong to each iteration
    // (`with beat(1):`, `with open_conn(cfg) as c:`); it is not a value to hoist.
    if (isWithItemExpression(node)) return null;

    const calleeText = func.text.trim();

    // Guard: volatile / non-pure calls
    if (isVolatileCall(calleeText)) return null;

    // Guard: C3.3 setup calls
    if (isC33SetupCallee(calleeText, aliases)) return null;

    // Guard: trivial O(1) built-ins
    if (isTrivialBuiltinCall(calleeText)) return null;

    // Collect all identifiers used in the call (callee and args)
    const identifiers = collectAllIdentifiers(node);
    if (identifiers.length === 0) return null;

    // Check every identifier against loop mutations
    for (const id of identifiers) {
      if (loop.assignedNames.has(id)) return null;
      if (loop.globalOrNonlocalNames.has(id)) return null;
      if (loop.touchedBases.has(id)) return null;

      // Check if argument was passed to an external call in the loop
      if (loop.callArguments.has(id)) {
        const calls = loop.callArguments.get(id)!;
        for (const c of calls) {
          if (c.id !== node.id && !isDescendantOf(c, node)) {
            // If the other call is a different function or outside this node, conservative suppress
            const otherFunc = c.childForFieldName("function")?.text.trim();
            if (otherFunc !== calleeText) {
              return null;
            }
          }
        }
      }
    }

    // Determine outermost invariant base symbol
    let symbol = "";
    const args = node.childForFieldName("arguments");
    if (args) {
      const argIds = collectAllIdentifiers(args);
      if (argIds.length > 0) {
        symbol = argIds[0];
      }
    }
    if (!symbol) {
      symbol = getRootIdentifier(func) || calleeText;
    }

    const expr = node.text.replace(/\s+/g, " ").trim();

    const limitations = ["hoist only if side-effect free — verify callee purity"];
    let confidence: Confidence = "medium";
    const opaque = loop.bodyNode ? opaqueStatementCall(loop.bodyNode) : null;
    if (opaque) {
      // The loop also calls `opaque()`, which can change closure/global state this call depends on.
      confidence = "low";
      limitations.push(
        `the loop also calls \`${opaque}()\`, whose effect on state this expression reads is not visible statically`
      );
    }

    return {
      node,
      signalType: "call",
      symbol,
      expr,
      severity: "medium",
      confidence,
      why: `Invariant call \`${expr}\` is recomputed on each iteration of the loop but its operands are defined outside the loop and never modified.`,
      limitations,
    };
  }

  if (node.type === "subscript" || node.type === "attribute") {
    // If the parent is also a subscript or attribute, let the outermost one handle it
    const parent = node.parent;
    if (parent && (parent.type === "subscript" || parent.type === "attribute")) {
      return null;
    }

    // A single lookup (`rate.value`, `cfg["k"]`) is C10.5's territory (lookup
    // overhead) and too cheap for the "value is costly" caveat; only chains of
    // two or more hops (`settings.limits["max"]`) are C3.2 candidates.
    if (chainDepth(node) < 2) return null;

    const identifiers = collectAllIdentifiers(node);
    if (identifiers.length === 0) return null;

    for (const id of identifiers) {
      if (loop.assignedNames.has(id)) return null;
      if (loop.globalOrNonlocalNames.has(id)) return null;
      if (loop.touchedBases.has(id)) return null;

      if (loop.callArguments.has(id)) {
        const calls = loop.callArguments.get(id)!;
        for (const c of calls) {
          if (!isDescendantOf(c, node)) {
            return null;
          }
        }
      }
    }

    const root = getRootIdentifier(node);
    const symbol = root || identifiers[0];
    const expr = node.text.replace(/\s+/g, " ").trim();

    return {
      node,
      signalType: "chain",
      symbol,
      expr,
      severity: "low",
      confidence: "medium",
      why: `Invariant attribute or subscript access \`${expr}\` is evaluated on each iteration of the loop but its base object is never modified.`,
      limitations: [
        "property getters and __getitem__ may perform computation or have side effects",
      ],
    };
  }

  if (node.type === "binary_operator") {
    // Check if operator is arithmetic
    const opChild = node.children.find((c) => ARITHMETIC_OPERATORS.has(c.text));
    if (!opChild) return null;

    // If parent is also an invariant binary operator, let parent be the outermost expression
    const parent = node.parent;
    if (parent && parent.type === "binary_operator") {
      const parentOp = parent.children.find((c) => ARITHMETIC_OPERATORS.has(c.text));
      if (parentOp) {
        const parentIds = collectAllIdentifiers(parent);
        const parentInvariant =
          parentIds.length > 0 &&
          !parentIds.some(
            (id) =>
              loop.assignedNames.has(id) ||
              loop.globalOrNonlocalNames.has(id) ||
              loop.touchedBases.has(id)
          );
        if (parentInvariant) {
          return null; // Inner part of larger invariant arithmetic
        }
      }
    }

    const identifiers = collectAllIdentifiers(node);
    if (identifiers.length === 0) return null; // constant expression

    for (const id of identifiers) {
      if (loop.assignedNames.has(id)) return null;
      if (loop.globalOrNonlocalNames.has(id)) return null;
      if (loop.touchedBases.has(id)) return null;

      if (loop.callArguments.has(id)) {
        const calls = loop.callArguments.get(id)!;
        for (const c of calls) {
          if (!isDescendantOf(c, node)) {
            return null;
          }
        }
      }
    }

    const symbol = identifiers[0];
    const expr = node.text.replace(/\s+/g, " ").trim();

    return {
      node,
      signalType: "arithmetic",
      symbol,
      expr,
      severity: "low",
      confidence: "high",
      why: `Invariant arithmetic calculation \`${expr}\` is recomputed on each iteration of the loop over unchanged outer variables.`,
      limitations: [],
    };
  }

  return null;
}

/**
 * Traverse Python AST and detect loop invariant recomputations (CODE-C3.2).
 */
export function detectLoopInvariants(
  rootNode: Parser.SyntaxNode,
  filePath: string,
  sourceLines: string[]
): Finding[] {
  const findings: Finding[] = [];
  const loops = collectLoops(rootNode, sourceLines);
  const aliases = collectImportAliases(rootNode);

  // Sort loops from outermost to innermost so nested loops attribute findings
  // to the outermost loop in which the expression is invariant.
  loops.sort((a, b) => {
    if (a.startLine !== b.startLine) {
      return a.startLine - b.startLine;
    }
    return b.endLine - a.endLine;
  });

  const reportedNodeSpans = new Set<string>();
  // Loops with identical headers in one scope get an ordinal so their
  // fingerprints differ; repeated occurrences of one expression in one loop
  // are reported once.
  const loopOrdinals = new Map<string, number>();

  for (const loop of loops) {
    if (loop.runsAtMostOnce || !loop.bodyNode) {
      continue;
    }

    const loopKey = `${loop.enclosingQualname}:${loop.headerText}`;
    const ordinal = loopOrdinals.get(loopKey) ?? 0;
    loopOrdinals.set(loopKey, ordinal + 1);
    const reportedExprs = new Set<string>();

    // Check if the loop header line itself is suppressed
    const loopHeaderSuppressed = isLineSuppressed(
      sourceLines[loop.startLine - 1] || "",
      ["CODE-C3.2"]
    ).isSuppressed;

    if (loopHeaderSuppressed) {
      continue;
    }

    function searchCandidates(n: Parser.SyntaxNode) {
      if (
        isInsideNestedDef(n, loop.bodyNode!) ||
        isInsideComprehension(n, loop.bodyNode!)
      ) {
        return;
      }

      const spanKey = `${n.startIndex}:${n.endIndex}`;
      if (reportedNodeSpans.has(spanKey)) {
        return;
      }

      const candidate = checkCandidateInvariance(n, loop, aliases);
      if (candidate && reportedExprs.has(candidate.expr)) {
        reportedNodeSpans.add(spanKey);
        return;
      }
      if (candidate) {
        const startLine = candidate.node.startPosition.row + 1;
        const endLine = candidate.node.endPosition.row + 1;
        const lineContent = sourceLines[startLine - 1] || candidate.node.text;

        // Check line suppression
        const suppression = isLineSuppressed(lineContent, ["CODE-C3.2"]);
        if (!suppression.isSuppressed) {
          const fingerprintId = `${loopKey}:${ordinal}:${candidate.expr}`;
          const fingerprint = generateFingerprint(
            "CODE-C3.2",
            "loop-invariant-recomputation",
            filePath,
            fingerprintId
          );

          const agentPrompt = `The expression \`${candidate.expr}\` appears invariant across loop iterations. Hoist it before the loop into a local variable (e.g. \`_hoisted_${candidate.symbol || "val"} = ${candidate.expr}\`) if it is pure and free of side effects.`;

          findings.push({
            check: "CODE-C3.2",
            kind: "loop-invariant-recomputation",
            fingerprint,
            identity: `loop-invariant-recomputation:${fingerprintId}`,
            location: {
              path: filePath,
              startLine,
              endLine,
            },
            evidence: {
              snippet: lineContent.trim(),
              symbol: candidate.symbol,
              expr: candidate.expr,
              loopType: loop.loopType,
            },
            why: candidate.why,
            severity: candidate.severity,
            confidence: candidate.confidence,
            limitations: candidate.limitations,
            evidenceTier: "static",
            impact: {
              quantified: false,
              reason:
                "Recomputing invariant expressions within loop body on every iteration adds CPU overhead; hoist outside loop before iteration begins.",
            },
            references: [
              {
                id: "SRC-01",
                title:
                  "Watts This Smell: A Comprehensive Taxonomy of Software Energy Smells",
                url: "https://arxiv.org/abs/2604.04809",
              },
              {
                id: "taxonomy-c3.2",
                title:
                  "Software Compute Waste Taxonomy — C3.2 Recomputing loop-invariant",
                url: "https://github.com/AWS-env/environmental-hacks/issues/61",
              },
            ],
            agentPrompt,
            detector: {
              id: "owner-a-static-scan",
              version: "0.1.0",
            },
          });

          reportedNodeSpans.add(spanKey);
          reportedExprs.add(candidate.expr);
        }
        return; // outermost invariant found, do not descend into children
      }

      for (const child of n.children) {
        searchCandidates(child);
      }
    }

    searchCandidates(loop.bodyNode);
  }

  return findings.sort((a, b) => a.location.startLine - b.location.startLine);
}
