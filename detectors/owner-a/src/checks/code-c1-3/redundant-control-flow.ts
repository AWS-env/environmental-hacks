import Parser from "tree-sitter";
import { Confidence, Finding, Severity, generateFingerprint } from "../../core/finding.js";
import { isLineSuppressed } from "../../core/suppressions.js";
import { getEnclosingQualname } from "../../core/loops.js";

const CHECK = "CODE-C1.3";
const IDENTICAL_KIND = "identical-branches";
const EMPTY_KIND = "empty-branch";
const DETECTOR_VERSION = "0.1.0";

const REFERENCES = [
  {
    id: "SRC-01",
    title:
      "Watts This Smell: A Comprehensive Taxonomy of Software Energy Smells",
    url: "https://arxiv.org/abs/2604.04809",
  },
  {
    id: "taxonomy-c1.3",
    title: "Software Compute Waste Taxonomy — C1.3 Redundant control flow",
    url: "https://github.com/AWS-env/environmental-hacks/issues/36",
  },
];

const NEGLIGIBLE_ALONE =
  "Static only: one redundant branch is negligible; it matters when the code runs in a hot loop, and trip count is not measured.";
const KEEP_THE_CONDITION =
  "A condition runs code (a call, `await`, walrus or attribute/subscript lookup); when collapsing the branch, keep it as a bare statement if its side effects are needed.";

/** Condition nodes that run code beyond reading names and literals. */
const EFFECT_TYPES = new Set(["call", "await", "yield", "named_expression"]);
/** Condition nodes whose evaluation is real work thrown away (raises severity). */
const WORK_TYPES = new Set(["call", "await"]);
/** Condition nodes that may run user code (`__getattr__`, `__getitem__`). */
const LOOKUP_TYPES = new Set(["attribute", "subscript"]);
/** Scope boundaries: a loop outside one of these does not repeat the code inside. */
const SCOPE_TYPES = new Set(["function_definition", "class_definition", "lambda", "module"]);
const COMPREHENSION_TYPES = new Set([
  "list_comprehension",
  "set_comprehension",
  "dictionary_comprehension",
  "generator_expression",
]);

/** One `if` / `elif` / `else` arm; `condition` is null for `else`. */
interface Arm {
  header: Parser.SyntaxNode;
  condition: Parser.SyntaxNode | null;
  body: Parser.SyntaxNode | null;
}

function statementsOf(block: Parser.SyntaxNode | null): Parser.SyntaxNode[] {
  return block ? block.namedChildren.filter((c) => c.type !== "comment") : [];
}

function contains(node: Parser.SyntaxNode, types: Set<string>): boolean {
  if (types.has(node.type)) return true;
  return node.namedChildren.some((c) => contains(c, types));
}

/** Leaf tokens, so `a[ i ]` matches `a[i]` and comments are ignored. */
function tokens(node: Parser.SyntaxNode | null): string[] {
  if (!node) return [];
  if (node.childCount === 0) return node.type === "comment" ? [] : [node.text];
  return node.children.flatMap(tokens);
}

function sameTokens(a: Parser.SyntaxNode | null, b: Parser.SyntaxNode | null): boolean {
  const ta = tokens(a);
  const tb = tokens(b);
  return ta.length === tb.length && ta.every((t, i) => t === tb[i]);
}

function normalized(node: Parser.SyntaxNode): string {
  return node.text.replace(/\s+/g, " ").trim();
}

function armsOf(ifNode: Parser.SyntaxNode): Arm[] {
  const arms: Arm[] = [
    {
      header: ifNode,
      condition: ifNode.childForFieldName("condition"),
      body: ifNode.childForFieldName("consequence"),
    },
  ];
  for (const alt of ifNode.childrenForFieldName("alternative")) {
    arms.push(
      alt.type === "elif_clause"
        ? { header: alt, condition: alt.childForFieldName("condition"), body: alt.childForFieldName("consequence") }
        : { header: alt, condition: null, body: alt.childForFieldName("body") }
    );
  }
  return arms;
}

/** A block of only `pass` / `...` statements. */
function isEmptyBlock(block: Parser.SyntaxNode | null): boolean {
  const stmts = statementsOf(block);
  return (
    stmts.length > 0 &&
    stmts.every(
      (s) =>
        s.type === "pass_statement" ||
        (s.type === "expression_statement" &&
          s.namedChildCount === 1 &&
          s.namedChildren[0].type === "ellipsis")
    )
  );
}

function headerText(arm: Arm): string {
  if (!arm.condition) return "else:";
  const keyword = arm.header.type === "elif_clause" ? "elif" : "if";
  return `${keyword} ${normalized(arm.condition)}:`;
}

function enclosingLoop(node: Parser.SyntaxNode): "for" | "while" | undefined {
  for (let curr = node.parent; curr && !SCOPE_TYPES.has(curr.type); curr = curr.parent) {
    if (curr.type === "for_statement" || COMPREHENSION_TYPES.has(curr.type)) return "for";
    if (curr.type === "while_statement") return "while";
  }
  return undefined;
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

interface Redundancy {
  kind: string;
  /** Node whose lines the finding spans. */
  start: Parser.SyntaxNode;
  end: Parser.SyntaxNode;
  /** Conditions evaluated without changing what runs. */
  conditions: Parser.SyntaxNode[];
  /** Lines where a `# noqa` suppresses the finding. */
  rows: number[];
  key: string;
  why: string;
  fix: string;
}

/** `if a: X elif b: X else: X` — every arm runs the same code. */
function identicalArms(ifNode: Parser.SyntaxNode, arms: Arm[]): Redundancy | null {
  if (arms.length < 2 || arms[arms.length - 1].condition) return null; // needs an `else`
  if (!arms.every((a) => sameTokens(a.body, arms[0].body))) return null;
  if (isEmptyBlock(arms[0].body)) return null; // reported as an empty branch
  const conditions = arms.flatMap((a) => (a.condition ? [a.condition] : []));
  const head = headerText(arms[0]);
  return {
    kind: IDENTICAL_KIND,
    start: ifNode,
    end: ifNode,
    conditions,
    rows: arms.map((a) => a.header.startPosition.row),
    key: arms.map(headerText).join("|"),
    why: `All ${arms.length} arms of \`${head}\` run the same code, so evaluating ${conditions
      .map((c) => `\`${normalized(c)}\``)
      .join(", ")} cannot change what runs`,
    fix: `collapse \`${head}\` into its shared body`,
  };
}

/** `if c: pass`, or empty trailing arms such as `if a: X elif b: pass`. */
function emptyArms(ifNode: Parser.SyntaxNode, arms: Arm[]): Redundancy | null {
  let first = arms.length;
  while (first > 0 && isEmptyBlock(arms[first - 1].body)) first--;
  const trailing = arms.slice(first);
  const conditions = trailing.flatMap((a) => (a.condition ? [a.condition] : []));
  if (conditions.length === 0) return null; // only an empty `else:` (no evaluation to save)

  const listed = conditions.map((c) => `\`${normalized(c)}\``).join(", ");
  const key = arms.map(headerText).join("|");
  const rows = trailing.map((a) => a.header.startPosition.row);
  if (first === 0) {
    const head = headerText(arms[0]);
    return {
      kind: EMPTY_KIND,
      start: ifNode,
      end: ifNode,
      conditions,
      rows,
      key,
      why: `\`${head}\` has only empty arms (\`pass\`/\`...\`), so ${listed} is evaluated and nothing depends on it`,
      fix: `remove the empty \`${head}\` statement (leave \`pass\` if it is the only statement in its block)`,
    };
  }
  const head = headerText(trailing[0]);
  return {
    kind: EMPTY_KIND,
    start: trailing[0].header,
    end: ifNode,
    conditions,
    rows,
    key: `${key}:${first}`,
    why: `The trailing \`${head}\` arm${trailing.length > 1 ? "s are" : " is"} empty: when it is taken nothing runs, the same as when it is not, so evaluating ${listed} is wasted`,
    fix: `delete the empty trailing arm${trailing.length > 1 ? "s" : ""} starting at \`${head}\``,
  };
}

/** `v if c else v` — both arms yield the same value. */
function identicalTernary(node: Parser.SyntaxNode): Redundancy | null {
  const [body, condition, alternative] = node.namedChildren.filter((c) => c.type !== "comment");
  if (!body || !condition || !alternative || !sameTokens(body, alternative)) return null;
  const expr = normalized(node);
  return {
    kind: IDENTICAL_KIND,
    start: node,
    end: node,
    conditions: [condition],
    rows: [node.startPosition.row],
    key: expr,
    why: `Both arms of \`${expr}\` are \`${normalized(body)}\`, so evaluating \`${normalized(condition)}\` cannot change the result`,
    fix: `replace \`${expr}\` with \`${normalized(body)}\``,
  };
}

/**
 * Pure function detecting control flow whose outcome does not depend on the
 * branch taken (CODE-C1.3): identical `if`/`elif`/`else` arms, identical
 * conditional-expression arms, and empty `if`/`elif` arms.
 * Never performs I/O or executes code.
 */
export function detectRedundantControlFlow(
  rootNode: Parser.SyntaxNode,
  filePath: string,
  sourceLines: string[]
): Finding[] {
  const findings: Finding[] = [];
  const next = ordinals();

  const report = (r: Redundancy) => {
    if (suppressed(sourceLines, r.rows)) return;
    const effectful = r.conditions.some((c) => contains(c, EFFECT_TYPES));
    const lookup = !effectful && r.conditions.some((c) => contains(c, LOOKUP_TYPES));
    const severity: Severity = r.conditions.some((c) => contains(c, WORK_TYPES)) ? "medium" : "low";
    const confidence: Confidence = effectful || lookup ? "medium" : "high";
    const limitations = effectful || lookup ? [NEGLIGIBLE_ALONE, KEEP_THE_CONDITION] : [NEGLIGIBLE_ALONE];

    const qualname = getEnclosingQualname(r.start);
    const key = `${qualname}:${r.key}`;
    const ordinal = next(`${r.kind}:${key}`);
    const startLine = r.start.startPosition.row + 1;
    const loopType = enclosingLoop(r.end);
    const conditionText = r.conditions.map(normalized).join(", ");

    findings.push({
      check: CHECK,
      kind: r.kind,
      fingerprint: generateFingerprint(CHECK, r.kind, filePath, `${key}:${ordinal}`),
      identity: `${r.kind}:${key}:${ordinal}`,
      location: { path: filePath, startLine, endLine: r.end.endPosition.row + 1 },
      evidence: {
        snippet: (sourceLines[r.start.startPosition.row] ?? "").trim(),
        expr: conditionText,
        ...(loopType ? { loopType } : {}),
      },
      why: `${r.why}${loopType ? ` on every iteration of the enclosing ${loopType} loop` : ""}.`,
      severity,
      confidence,
      limitations,
      evidenceTier: "static",
      impact: {
        quantified: false,
        reason:
          "Saved work = cost of evaluating the redundant condition × how often it runs; neither is measured statically.",
      },
      references: REFERENCES.map((ref) => ({ ...ref })),
      agentPrompt: `In ${filePath}:${startLine}, ${r.fix}${
        effectful || lookup
          ? `; keep \`${conditionText}\` as a bare statement only if its side effects are needed`
          : ""
      }. Static finding only — keep the change minimal and covered by tests.`,
      detector: { id: "owner-a-static-scan", version: DETECTOR_VERSION },
    });
  };

  const visit = (node: Parser.SyntaxNode) => {
    if (node.type === "if_statement") {
      const arms = armsOf(node);
      const found = identicalArms(node, arms) ?? emptyArms(node, arms);
      if (found) report(found);
    } else if (node.type === "conditional_expression") {
      const found = identicalTernary(node);
      if (found) report(found);
    }
    for (const child of node.namedChildren) visit(child);
  };

  visit(rootNode);
  return findings.sort((a, b) => a.location.startLine - b.location.startLine);
}
