import Parser from "tree-sitter";
import { Confidence, Finding, generateFingerprint } from "../../core/finding.js";
import { isLineSuppressed } from "../../core/suppressions.js";
import {
  LoopInfo,
  collectAllIdentifiers,
  collectLoops,
  isVolatileCall,
} from "../../core/loops.js";

const CHECK = "CODE-C3.5";
const KIND = "missing-early-exit";
const DETECTOR_VERSION = "0.1.0";

const REFERENCES = [
  {
    id: "SRC-01",
    title:
      "Watts This Smell: An Empirical Study on Energy Smells in Python Software",
    url: "https://arxiv.org/abs/2604.04809",
  },
  {
    id: "taxonomy-c3.5",
    title: "Software Compute Waste Taxonomy — C3.5 Missing loop early exit",
    url: "https://github.com/AWS-env/environmental-hacks/issues/64",
  },
];

const CONDITIONAL_PAYOFF =
  "Static only: saves the remaining iterations only when the match lands early; match position and trip count are not measured.";
const FIRST_VS_LAST =
  "Breaking yields the first match; the loop currently keeps the last. Verify only the first match is wanted (or that at most one element can match).";
const LAZY_SOURCE =
  "If the iterable is a generator or stream with side effects, stopping early also skips producing the remaining elements.";

const LITERAL_TYPES = new Set([
  "true",
  "false",
  "none",
  "integer",
  "float",
  "string",
]);

type Signal = "flag" | "store" | "store-return";

interface Arm {
  ifNode: Parser.SyntaxNode;
  condition: Parser.SyntaxNode;
  stores: { target: string; value: Parser.SyntaxNode; row: number }[];
}

function statementsOf(block: Parser.SyntaxNode | null): Parser.SyntaxNode[] {
  return block ? block.namedChildren.filter((c) => c.type !== "comment") : [];
}

/** `target = value` as a plain statement, else null. */
function plainAssignment(
  stmt: Parser.SyntaxNode
): { target: string; value: Parser.SyntaxNode } | null {
  if (stmt.type !== "expression_statement") return null;
  const inner = statementsOf(stmt);
  if (inner.length !== 1 || inner[0].type !== "assignment") return null;
  const left = inner[0].childForFieldName("left");
  const right = inner[0].childForFieldName("right");
  if (left?.type !== "identifier" || !right) return null;
  return { target: left.text, value: right };
}

/**
 * The loop body must be exactly one `if` (no `elif` / `else`) whose consequence
 * only assigns names: any other statement is per-element work that an early
 * exit would skip, and an `else` branch is a reset.
 */
function matchArm(loop: LoopInfo): Arm | null {
  const stmts = statementsOf(loop.bodyNode);
  if (stmts.length !== 1 || stmts[0].type !== "if_statement") return null;
  const ifNode = stmts[0];
  if (ifNode.childForFieldName("alternative")) return null;
  const condition = ifNode.childForFieldName("condition");
  const consequence = ifNode.childForFieldName("consequence");
  if (!condition || !consequence) return null;

  const stores: Arm["stores"] = [];
  for (const stmt of statementsOf(consequence)) {
    const assignment = plainAssignment(stmt);
    if (!assignment) return null; // break/return/raise/call/augmented/... → not ours
    stores.push({ ...assignment, row: stmt.startPosition.row });
  }
  return stores.length > 0 ? { ifNode, condition, stores } : null;
}

/** Names assigned in the enclosing scope before `loop` starts (nested defs excluded). */
function namesBoundBefore(loopNode: Parser.SyntaxNode): Set<string> {
  let scope: Parser.SyntaxNode | null = loopNode.parent;
  while (scope && scope.type !== "function_definition" && scope.type !== "module") {
    scope = scope.parent;
  }
  const names = new Set<string>();
  if (!scope) return names;
  const visit = (node: Parser.SyntaxNode) => {
    for (const child of node.namedChildren) {
      if (child.startIndex >= loopNode.startIndex) return;
      if (child.type === "function_definition" || child.type === "class_definition") {
        continue;
      }
      if (child.type === "assignment") {
        const left = child.childForFieldName("left");
        if (left) for (const id of collectAllIdentifiers(left)) names.add(id);
      }
      visit(child);
    }
  };
  if (scope.type === "function_definition") {
    const params = scope.childForFieldName("parameters");
    if (params) for (const id of collectAllIdentifiers(params)) names.add(id);
    const body = scope.childForFieldName("body");
    if (body) visit(body);
  } else {
    visit(scope);
  }
  return names;
}

function hasVolatileCall(node: Parser.SyntaxNode): boolean {
  if (node.type === "call") {
    const fn = node.childForFieldName("function");
    if (fn && isVolatileCall(fn.text)) return true;
  }
  return node.namedChildren.some(hasVolatileCall);
}

/** The statement right after the loop is `return <target>`. */
function returnsTargetAfter(loopNode: Parser.SyntaxNode, targets: Set<string>): string | null {
  const next = loopNode.nextNamedSibling;
  if (!next || next.type !== "return_statement") return null;
  const value = next.namedChildren[0];
  return value?.type === "identifier" && targets.has(value.text) ? value.text : null;
}

function classify(
  loop: LoopInfo,
  arm: Arm
): { signal: Signal; confidence: Confidence; symbol: string } | null {
  const loopNode = loop.loopNode;
  const targets = new Set(arm.stores.map((s) => s.target));
  const bound = namesBoundBefore(loopNode);

  // Every flag/store target is initialised before the loop.
  for (const t of targets) if (!bound.has(t)) return null;

  // The flag/result must not be read in the loop: in the match test, in a
  // stored value, or in a `while` condition (which would already be an exit).
  const readsTarget = (node: Parser.SyntaxNode | null) =>
    node !== null && collectAllIdentifiers(node).some((id) => targets.has(id));
  if (readsTarget(arm.condition)) return null;
  if (arm.stores.some((s) => readsTarget(s.value))) return null;
  if (loop.loopType === "while" && readsTarget(loopNode.childForFieldName("condition"))) {
    return null;
  }

  // `for ... else` is the idiomatic break-aware form; leave it alone.
  if (loopNode.childForFieldName("alternative")) return null;

  // Volatile calls on the match path make "stop early" observable.
  if (hasVolatileCall(arm.condition) || arm.stores.some((s) => hasVolatileCall(s.value))) {
    return null;
  }

  const symbol = [...targets].join(", ");
  const allLiteral = arm.stores.every((s) => LITERAL_TYPES.has(s.value.type));
  if (allLiteral) return { signal: "flag", confidence: "high", symbol };

  const returned = returnsTargetAfter(loopNode, targets);
  if (returned) return { signal: "store-return", confidence: "medium", symbol: returned };
  return { signal: "store", confidence: "medium", symbol };
}

function promptFor(signal: Signal, symbol: string, header: string): string {
  switch (signal) {
    case "flag":
      return `add \`break\` right after setting '${symbol}' (the value is sticky, so stopping at the first match keeps the result), or replace the loop with \`${symbol} = any(<condition> ${header.replace(/^(async\s+)?for\s+/, "for ")})\``;
    case "store":
      return `add \`break\` after storing '${symbol}' if only the first match is wanted (the loop currently keeps the last match), or use \`${symbol} = next((<value> ${header.replace(/^(async\s+)?for\s+/, "for ")} if <condition>), ${symbol})\``;
    case "store-return":
      return `return the match from inside the loop (\`return <value>\`) instead of storing '${symbol}' and returning it after the loop, after confirming the first match is the one wanted (the loop currently returns the last)`;
  }
}

/**
 * Pure function detecting loops that keep iterating after their result is
 * decided (CODE-C3.5). Never performs I/O or executes code.
 */
export function detectMissingEarlyExit(
  rootNode: Parser.SyntaxNode,
  filePath: string,
  sourceLines: string[]
): Finding[] {
  const findings: Finding[] = [];
  const loopOrdinals = new Map<string, number>();

  for (const loop of collectLoops(rootNode, sourceLines)) {
    const loopKey = `${loop.enclosingQualname}:${loop.headerText}`;
    const ordinal = loopOrdinals.get(loopKey) ?? 0;
    loopOrdinals.set(loopKey, ordinal + 1);

    const arm = matchArm(loop);
    if (!arm) continue;
    const match = classify(loop, arm);
    if (!match) continue;

    const rows = [loop.startLine - 1, ...arm.stores.map((s) => s.row)];
    if (rows.some((r) => isLineSuppressed(sourceLines[r] ?? "", [CHECK]).isSuppressed)) {
      continue;
    }

    const limitations = [CONDITIONAL_PAYOFF, LAZY_SOURCE];
    if (match.signal !== "flag") limitations.push(FIRST_VS_LAST);

    const header = loop.headerText;
    const why =
      match.signal === "flag"
        ? `Loop '${header}' sets '${match.symbol}' on a match but keeps iterating; the value cannot change after the first match, so every later iteration is wasted.`
        : `Loop '${header}' stores '${match.symbol}' on a match but never exits; it scans the whole collection even when the first match is all that is needed.`;

    findings.push({
      check: CHECK,
      kind: KIND,
      fingerprint: generateFingerprint(
        CHECK,
        KIND,
        filePath,
        `${loopKey}:${ordinal}:${match.symbol}`
      ),
      identity: `${KIND}:${loopKey}:${ordinal}:${match.symbol}`,
      location: { path: filePath, startLine: loop.startLine, endLine: loop.endLine },
      evidence: {
        snippet: (sourceLines[loop.startLine - 1] ?? "").trim(),
        symbol: match.symbol,
        loopType: loop.loopType,
      },
      why,
      severity: "medium",
      confidence: match.confidence,
      limitations,
      evidenceTier: "static",
      impact: {
        quantified: false,
        reason:
          "Saved work = iterations after the first match × per-iteration cost; neither is measured statically.",
      },
      references: REFERENCES.map((r) => ({ ...r })),
      agentPrompt: `In ${filePath}:${loop.startLine}-${loop.endLine}, ${promptFor(match.signal, match.symbol, header)}. Static finding only — keep the change minimal and covered by tests.`,
      detector: { id: "owner-a-static-scan", version: DETECTOR_VERSION },
    });
  }

  return findings.sort((a, b) => a.location.startLine - b.location.startLine);
}
