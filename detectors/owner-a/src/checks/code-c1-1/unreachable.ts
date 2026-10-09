import Parser from "tree-sitter";
import {
  Finding,
  generateFingerprint,
  Severity,
  Confidence,
} from "../../core/finding.js";
import { isLineSuppressed } from "../../core/suppressions.js";

const TERMINAL_NODE_TYPES = new Set([
  "return_statement",
  "raise_statement",
  "break_statement",
  "continue_statement",
]);

const CONSTANT_FALSE_CONDITIONS = new Set([
  "False",
  "0",
  "None",
  '""',
  "''",
  "[]",
  "{}",
]);

export function detectUnreachableCode(
  rootNode: Parser.SyntaxNode,
  filePath: string,
  sourceLines: string[]
): Finding[] {
  const findings: Finding[] = [];

  function isStatement(node: Parser.SyntaxNode): boolean {
    if (node.type === "comment") return false;
    // Check if it's an executable statement node
    return node.isNamed;
  }

  function checkBlock(blockNode: Parser.SyntaxNode) {
    let seenTerminal: Parser.SyntaxNode | null = null;

    for (const child of blockNode.namedChildren) {
      if (child.type === "comment") continue;

      if (seenTerminal) {
        // Child is unreachable dead code
        const startLine = child.startPosition.row + 1;
        const endLine = child.endPosition.row + 1;
        const lineText = sourceLines[child.startPosition.row] || child.text;

        const suppression = isLineSuppressed(lineText, ["F401", "CODE-C1.1"]);
        if (!suppression.isSuppressed) {
          const snippet = lineText.trim();
          const why = `Statement is unreachable because it follows a terminal '${seenTerminal.type.replace("_statement", "")}' statement in the same block.`;
          const fingerprint = generateFingerprint(
            "CODE-C1.1",
            "unreachable-code",
            filePath,
            `after-${seenTerminal.type}:${startLine}`
          );

          findings.push({
            check: "CODE-C1.1",
            kind: "unreachable-code",
            fingerprint,
            location: {
              path: filePath,
              startLine,
              endLine,
            },
            evidence: {
              snippet,
            },
            why,
            severity: "info" as Severity,
            confidence: "high" as Confidence,
            limitations: [
              "Static single-pass AST reachability analysis without dynamic flow graph solving.",
            ],
            evidenceTier: "static",
            impact: {
              quantified: false,
              reason:
                "Unreachable code is compiled but never executed; impact is limited to parsing and bytecode storage overhead.",
            },
            references: [
              {
                id: "SRC-01",
                title: "Watts This Smell: An Empirical Study on Energy Smells in Python Software",
                url: "https://arxiv.org/abs/2604.04809",
              },
            ],
            agentPrompt: `In ${filePath}:${startLine}, remove unreachable code ("${snippet}") following '${seenTerminal.type.replace("_statement", "")}' to clean up dead code.`,
            detector: {
              id: "owner-a-static-scan",
              version: "0.1.0",
            },
          });
        }
      }

      if (TERMINAL_NODE_TYPES.has(child.type)) {
        seenTerminal = child;
      }
    }
  }

  function visit(node: Parser.SyntaxNode) {
    if (node.type === "block") {
      checkBlock(node);
    }

    // Check if_statement with constant false condition
    if (node.type === "if_statement") {
      const condition = node.childForFieldName("condition");
      if (condition && CONSTANT_FALSE_CONDITIONS.has(condition.text.trim())) {
        const consequence = node.childForFieldName("consequence");
        if (consequence) {
          const startLine = consequence.startPosition.row + 1;
          const endLine = consequence.endPosition.row + 1;
          const lineText = sourceLines[consequence.startPosition.row] || consequence.text;

          const suppression = isLineSuppressed(lineText, ["F401", "CODE-C1.1"]);
          if (!suppression.isSuppressed) {
            const snippet = lineText.trim();
            const fingerprint = generateFingerprint(
              "CODE-C1.1",
              "unreachable-code",
              filePath,
              `const-false:${startLine}`
            );

            findings.push({
              check: "CODE-C1.1",
              kind: "unreachable-code",
              fingerprint,
              location: {
                path: filePath,
                startLine,
                endLine,
              },
              evidence: {
                snippet,
              },
              why: `Block is guarded by constant condition '${condition.text.trim()}' and can never be reached.`,
              severity: "info" as Severity,
              confidence: "high" as Confidence,
              limitations: [
                "Static AST condition check detects literal false conditions only.",
              ],
              evidenceTier: "static",
              impact: {
                quantified: false,
                reason:
                  "Unreachable branch is compiled but never executed; impact is limited to parsing and maintenance overhead.",
              },
              references: [
                {
                  id: "SRC-01",
                  title: "Watts This Smell: An Empirical Study on Energy Smells in Python Software",
                  url: "https://arxiv.org/abs/2604.04809",
                },
              ],
              agentPrompt: `In ${filePath}:${startLine}, remove dead block guarded by constant false condition '${condition.text.trim()}'.`,
              detector: {
                id: "owner-a-static-scan",
                version: "0.1.0",
              },
            });
          }
        }
      }
    }

    // Check while_statement with constant false condition
    if (node.type === "while_statement") {
      const condition = node.childForFieldName("condition");
      if (condition && CONSTANT_FALSE_CONDITIONS.has(condition.text.trim())) {
        const body = node.childForFieldName("body");
        if (body) {
          const startLine = body.startPosition.row + 1;
          const endLine = body.endPosition.row + 1;
          const lineText = sourceLines[body.startPosition.row] || body.text;

          const suppression = isLineSuppressed(lineText, ["F401", "CODE-C1.1"]);
          if (!suppression.isSuppressed) {
            const snippet = lineText.trim();
            const fingerprint = generateFingerprint(
              "CODE-C1.1",
              "unreachable-code",
              filePath,
              `while-false:${startLine}`
            );

            findings.push({
              check: "CODE-C1.1",
              kind: "unreachable-code",
              fingerprint,
              location: {
                path: filePath,
                startLine,
                endLine,
              },
              evidence: {
                snippet,
              },
              why: `While loop is guarded by constant false condition '${condition.text.trim()}' and will never iterate.`,
              severity: "info" as Severity,
              confidence: "high" as Confidence,
              limitations: [
                "Static AST condition check detects literal false conditions only.",
              ],
              evidenceTier: "static",
              impact: {
                quantified: false,
                reason:
                  "Unreachable loop body is compiled but never executed.",
              },
              references: [
                {
                  id: "SRC-01",
                  title: "Watts This Smell: An Empirical Study on Energy Smells in Python Software",
                  url: "https://arxiv.org/abs/2604.04809",
                },
              ],
              agentPrompt: `In ${filePath}:${startLine}, remove dead while loop guarded by constant false condition '${condition.text.trim()}'.`,
              detector: {
                id: "owner-a-static-scan",
                version: "0.1.0",
              },
            });
          }
        }
      }
    }

    for (const child of node.namedChildren) {
      visit(child);
    }
  }

  visit(rootNode);

  return findings;
}
