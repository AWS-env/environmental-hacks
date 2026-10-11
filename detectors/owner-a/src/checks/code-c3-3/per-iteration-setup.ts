import Parser from "tree-sitter";
import {
  Confidence,
  CostTier,
  Finding,
  Severity,
  generateFingerprint,
} from "../../core/finding.js";
import { isLineSuppressed } from "../../core/suppressions.js";
import {
  LoopInfo,
  SetupSignal,
  collectAllIdentifiers,
  collectImportAliases,
  collectLoops,
  resolveCallee,
  setupSignalFor,
} from "../../core/loops.js";

const CHECK = "CODE-C3.3";
const KIND = "per-iteration-setup";
const DETECTOR_VERSION = "0.1.0";

const REFERENCES = [
  {
    id: "SRC-01",
    title:
      "Watts This Smell: A Comprehensive Taxonomy of Software Energy Smells",
    url: "https://arxiv.org/abs/2604.04809",
  },
  {
    id: "taxonomy-c3.3",
    title: "Software Compute Waste Taxonomy — C3.3 Inefficient per-iteration setup",
    url: "https://github.com/AWS-env/environmental-hacks/issues/62",
  },
];

const FRESHNESS_LIMITATION =
  "Hoist only if the object is used read-only or is safe to share across iterations — verify close/lifecycle and per-iteration state.";

/**
 * CapWords callees that are cheap value or scratch objects, where a fresh
 * instance per iteration is normal (Tier B never reports these).
 */
const CHEAP_CAPWORDS = new Set([
  "Counter",
  "OrderedDict",
  "ChainMap",
  "StringIO",
  "BytesIO",
  "Decimal",
  "Fraction",
  "Path",
  "PurePath",
  "PosixPath",
  "WindowsPath",
  "Lock",
  "RLock",
  "Event",
  "Condition",
  "Semaphore",
  "Queue",
  "SimpleNamespace",
  "UUID",
]);

/** Method names that fill or reset an object — the scratch-buffer pattern. */
const SCRATCH_MUTATORS = new Set([
  "append",
  "add",
  "extend",
  "insert",
  "update",
  "write",
  "writelines",
  "put",
  "push",
  "clear",
  "reset",
  "feed",
  "setdefault",
]);

const COMPREHENSIONS = new Set([
  "list_comprehension",
  "set_comprehension",
  "dictionary_comprehension",
  "generator_expression",
  "lambda",
]);

interface SignalProfile {
  label: string;
  severity: Severity;
  confidence: Confidence;
  costTier: CostTier;
  why: (factory: string) => string;
  lifecycle: string;
}

const PROFILES: Record<SetupSignal | "construction", SignalProfile> = {
  compile: {
    label: "pattern/template compile",
    severity: "medium",
    confidence: "high",
    costTier: "heavy",
    why: (f) =>
      `'${f}(...)' recompiles the same pattern/template on every iteration; compiled objects are immutable, so one compile before the loop serves every iteration.`,
    lifecycle: "Compiled patterns/templates are immutable; no cleanup needed.",
  },
  "cached-compile": {
    label: "regex compile (cached by re)",
    severity: "low",
    confidence: "medium",
    costTier: "light",
    why: (f) =>
      `'${f}(...)' is called with loop-invariant arguments on every iteration; the re module caches recently compiled patterns, so each repeat costs a cache lookup rather than a recompile. Hoisting still removes that lookup and states the intent, but the saving is small.`,
    lifecycle: "Compiled patterns are immutable; no cleanup needed.",
  },
  connection: {
    label: "connection/session/client/pool",
    severity: "high",
    confidence: "medium",
    costTier: "heavy",
    why: (f) =>
      `'${f}(...)' opens a new connection/session/client/pool on every iteration (handshakes, pool setup, thread spawn); one instance created before the loop can be reused for every iteration.`,
    lifecycle:
      "Create it once before the loop (ideally `with ... as client:` around the loop) and close it once after; check thread-safety if iterations run concurrently.",
  },
  "file-open": {
    label: "file open",
    severity: "medium",
    confidence: "medium",
    costTier: "heavy",
    why: (f) =>
      `'${f}(...)' re-opens (and typically re-reads) the same file on every iteration; read it once before the loop unless the file changes between iterations.`,
    lifecycle:
      "Open/read once before the loop; confirm the file is not modified by the loop or another process mid-loop.",
  },
  construction: {
    label: "object construction",
    severity: "low",
    confidence: "medium",
    costTier: "unknown",
    why: (f) =>
      `'${f}(...)' constructs an object from loop-invariant arguments on every iteration; if construction is costly and the object is not mutated per iteration, build it once before the loop.`,
    lifecycle:
      "Hoist only if construction is costly and the instance is not mutated or relied on being fresh each iteration.",
  },
};

interface SetupSite {
  call: Parser.SyntaxNode;
  /** Name bound to the result (assignment target / `with ... as`), if any. */
  boundName: string | null;
  factory: string;
  signal: SetupSignal | "construction";
}

/** Literal mode string of an `open(...)` call, if statically known. */
function openMode(call: Parser.SyntaxNode): string | null {
  const args = call.childForFieldName("arguments");
  if (!args) return null;
  const positional = args.namedChildren.filter(
    (a) => a.type !== "keyword_argument" && a.type !== "comment"
  );
  const kw = args.namedChildren.find(
    (a) =>
      a.type === "keyword_argument" &&
      a.childForFieldName("name")?.text === "mode"
  );
  const modeNode = kw ? kw.childForFieldName("value") : positional[1];
  if (!modeNode || modeNode.type !== "string") return null;
  return modeNode.text.replace(/^[rbuRBU]*['"]{1,3}|['"]{1,3}$/g, "");
}

function boundNameOf(call: Parser.SyntaxNode): string | null {
  const parent = call.parent;
  if (!parent) return null;
  if (parent.type === "assignment") {
    const left = parent.childForFieldName("left");
    return left?.type === "identifier" ? left.text : null;
  }
  if (parent.type === "as_pattern") {
    const alias = parent.childForFieldName("alias");
    return alias ? alias.text.replace(/[()]/g, "") : null;
  }
  return null;
}

/** File-local `def Foo(...)`: a CapWords *function*, not a class. */
function collectCapWordsFunctions(rootNode: Parser.SyntaxNode): Set<string> {
  const names = new Set<string>();
  const visit = (node: Parser.SyntaxNode) => {
    if (node.type === "function_definition") {
      const name = node.childForFieldName("name")?.text;
      if (name && /^[A-Z]/.test(name)) names.add(name);
    }
    for (const child of node.namedChildren) visit(child);
  };
  visit(rootNode);
  return names;
}

function classifySite(
  call: Parser.SyntaxNode,
  aliases: ReadonlyMap<string, string>,
  capWordsFunctions: Set<string>
): SetupSite | null {
  const fn = call.childForFieldName("function");
  if (!fn) return null;
  const factory = resolveCallee(fn.text, aliases);
  const boundName = boundNameOf(call);

  const signal = setupSignalFor(factory);
  if (signal) {
    if (signal === "file-open") {
      const mode = openMode(call);
      // Writes/appends per item are fragmented I/O (C9.1), not a re-read.
      if (mode && /[wax+]/.test(mode)) return null;
    }
    return { call, boundName, factory, signal };
  }

  // Tier B: CapWords construction, only where the result is kept.
  const last = factory.split(".").pop() ?? "";
  if (!/^[A-Z][a-z0-9]+[A-Za-z0-9]*$/.test(last)) return null; // CapWords, not CONSTANT
  if (CHEAP_CAPWORDS.has(last)) return null;
  if (/(Error|Exception|Warning|Exit|Interrupt)$/.test(last)) return null;
  if (capWordsFunctions.has(fn.text)) return null;
  if (boundName === null) return null;
  return { call, boundName, factory, signal: "construction" };
}

/** True when every name the call's arguments read is unchanged by the loop. */
function argumentsInvariant(call: Parser.SyntaxNode, loop: LoopInfo): boolean {
  const args = call.childForFieldName("arguments");
  const ids = args ? collectAllIdentifiers(args) : [];
  // Keyword names (`mode=`) are labels, not reads.
  const keywordNames = new Set(
    (args?.namedChildren ?? [])
      .filter((a) => a.type === "keyword_argument")
      .map((a) => a.childForFieldName("name")?.text)
  );
  return ids.every(
    (id) =>
      keywordNames.has(id) ||
      (!loop.assignedNames.has(id) &&
        !loop.globalOrNonlocalNames.has(id) &&
        !loop.touchedBases.has(id))
  );
}

/** The bound object is filled/reset in the loop — a per-iteration scratch buffer. */
function isScratchObject(name: string, loop: LoopInfo): boolean {
  if (!loop.bodyNode) return false;
  const visit = (node: Parser.SyntaxNode): boolean => {
    if (node.type === "call") {
      const fn = node.childForFieldName("function");
      if (
        fn?.type === "attribute" &&
        fn.childForFieldName("object")?.text === name &&
        SCRATCH_MUTATORS.has(fn.childForFieldName("attribute")?.text ?? "")
      ) {
        return true;
      }
    }
    if (node.type === "assignment" || node.type === "augmented_assignment") {
      const left = node.childForFieldName("left");
      if (
        (left?.type === "attribute" &&
          left.childForFieldName("object")?.text === name) ||
        (left?.type === "subscript" &&
          left.childForFieldName("value")?.text === name)
      ) {
        return true;
      }
    }
    return node.namedChildren.some(visit);
  };
  return visit(loop.bodyNode);
}

const CONTAINER_TYPES = new Set([
  "list",
  "tuple",
  "set",
  "dictionary",
  "pair",
  "parenthesized_expression",
  "list_splat",
  "keyword_argument",
]);

/**
 * The bound object is handed to something that outlives the iteration: passed to a method of another
 * object (`out.append(m)`, `app.add_subapp(p, m)`), stored into an attribute or subscript (`d[k] = m`),
 * or yielded/returned. A new object per item is then the point, not a setup cost to hoist.
 */
function escapesIteration(name: string, loop: LoopInfo): boolean {
  if (!loop.bodyNode) return false;
  const escapes = (id: Parser.SyntaxNode): boolean => {
    let node: Parser.SyntaxNode = id;
    while (node.parent && CONTAINER_TYPES.has(node.parent.type)) node = node.parent;
    const parent = node.parent;
    if (!parent) return false;
    if (parent.type === "yield" || parent.type === "return_statement") return true;
    if (parent.type === "assignment") {
      const left = parent.childForFieldName("left");
      return parent.childForFieldName("right")?.id === node.id && (left?.type === "attribute" || left?.type === "subscript");
    }
    if (parent.type === "argument_list") {
      const fn = parent.parent?.childForFieldName("function");
      return fn?.type === "attribute" && fn.childForFieldName("object")?.text !== name;
    }
    return false;
  };
  const visit = (node: Parser.SyntaxNode): boolean => {
    if (node.type === "identifier" && node.text === name && escapes(node)) return true;
    return node.namedChildren.some(visit);
  };
  return visit(loop.bodyNode);
}

/** `with <call>:` / `with <call> as x:` — the manager's lifecycle is the iteration. */
function isWithItemCall(call: Parser.SyntaxNode): boolean {
  const parent = call.parent;
  if (!parent) return false;
  return parent.type === "with_item" || (parent.type === "as_pattern" && parent.parent?.type === "with_item");
}

/** Setup call sites directly owned by this loop body (not comprehensions / nested defs). */
function collectCalls(loop: LoopInfo): Parser.SyntaxNode[] {
  const calls: Parser.SyntaxNode[] = [];
  const body = loop.bodyNode;
  if (!body) return calls;
  const visit = (node: Parser.SyntaxNode) => {
    for (const child of node.namedChildren) {
      if (
        child.type === "function_definition" ||
        child.type === "class_definition" ||
        COMPREHENSIONS.has(child.type)
      ) {
        continue;
      }
      if (child.type === "call") calls.push(child);
      visit(child);
    }
  };
  visit(body);
  return calls;
}

/**
 * Pure function detecting heavyweight setup repeated on every loop iteration
 * (CODE-C3.3). Never performs I/O or executes code.
 */
export function detectPerIterationSetup(
  rootNode: Parser.SyntaxNode,
  filePath: string,
  sourceLines: string[]
): Finding[] {
  const findings: Finding[] = [];
  const aliases = collectImportAliases(rootNode);
  const capWordsFunctions = collectCapWordsFunctions(rootNode);
  const loops = collectLoops(rootNode, sourceLines);
  // Outermost first, so a call invariant in several nested loops is reported
  // once, against the outermost loop it can be hoisted out of.
  loops.sort((a, b) => a.startLine - b.startLine || b.endLine - a.endLine);

  const reported = new Set<number>();
  const loopOrdinals = new Map<string, number>();

  for (const loop of loops) {
    if (!loop.bodyNode || loop.runsAtMostOnce) continue;

    const loopKey = `${loop.enclosingQualname}:${loop.headerText}`;
    const loopOrdinal = loopOrdinals.get(loopKey) ?? 0;
    loopOrdinals.set(loopKey, loopOrdinal + 1);
    const factoryOrdinals = new Map<string, number>();

    const headerSuppressed = isLineSuppressed(
      sourceLines[loop.startLine - 1] ?? "",
      [CHECK]
    ).isSuppressed;

    for (const call of collectCalls(loop)) {
      if (reported.has(call.startIndex)) continue;
      if (call.parent?.type === "raise_statement") continue;
      const site = classifySite(call, aliases, capWordsFunctions);
      if (!site) continue;
      if (!argumentsInvariant(call, loop)) continue;
      if (
        site.signal === "construction" &&
        site.boundName &&
        isScratchObject(site.boundName, loop)
      ) {
        continue;
      }
      if (site.signal === "construction") {
        // A fresh object per item that is stored/handed on, or a with-managed one, is not hoistable setup.
        if (isWithItemCall(call)) continue;
        if (site.boundName && escapesIteration(site.boundName, loop)) continue;
      }

      reported.add(call.startIndex);
      const row = call.startPosition.row;
      const line = sourceLines[row] ?? "";
      if (headerSuppressed || isLineSuppressed(line, [CHECK]).isSuppressed) {
        continue;
      }

      const profile = PROFILES[site.signal];
      const factoryOrdinal = factoryOrdinals.get(site.factory) ?? 0;
      factoryOrdinals.set(site.factory, factoryOrdinal + 1);
      const identityKey = `${loopKey}:${loopOrdinal}:${site.factory}:${factoryOrdinal}`;
      const fingerprint = generateFingerprint(CHECK, KIND, filePath, identityKey);
      const startLine = row + 1;
      const endLine = call.endPosition.row + 1;
      const limitations = [
        "Static only: setup cost and trip count are not measured; the payoff scales with both.",
        FRESHNESS_LIMITATION,
      ];

      findings.push({
        check: CHECK,
        kind: KIND,
        fingerprint,
        identity: `${KIND}:${identityKey}`,
        location: { path: filePath, startLine, endLine },
        evidence: {
          snippet: line.trim(),
          symbol: site.boundName ?? site.factory,
          factory: site.factory,
          costTier: profile.costTier,
          loopType: loop.loopType,
        },
        why: profile.why(site.factory),
        severity: profile.severity,
        confidence: profile.confidence,
        limitations,
        evidenceTier: "static",
        impact: {
          quantified: false,
          reason:
            "Repeated setup cost × loop trip count; neither is measured statically.",
        },
        references: REFERENCES.map((r) => ({ ...r })),
        agentPrompt: `In ${filePath}:${startLine}, the ${profile.label} '${site.factory}(...)' runs on every iteration of the loop at line ${loop.startLine} ("${loop.headerText}") with loop-invariant arguments. Hoist it before the loop and reuse it. ${profile.lifecycle} Keep per-iteration construction if the object must be fresh each time. Keep the change minimal and covered by tests.`,
        detector: { id: "owner-a-static-scan", version: DETECTOR_VERSION },
      });
    }
  }

  return findings.sort((a, b) => a.location.startLine - b.location.startLine);
}
