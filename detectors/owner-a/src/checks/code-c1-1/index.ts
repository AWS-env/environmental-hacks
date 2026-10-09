import { Finding, DetectorContext } from "../../core/finding.js";
import { ParsedPythonFile } from "../../core/parse.js";
import { analyzePythonScope } from "../../core/python-scope.js";
import { detectUnusedImports } from "./unused-imports.js";
import { detectUnreachableCode } from "./unreachable.js";

export { detectUnusedImports } from "./unused-imports.js";
export { detectUnreachableCode } from "./unreachable.js";

/**
 * Pure function executing CODE-C1.1 check on an already-parsed Python file AST.
 * Never performs I/O or executes code.
 */
export function checkCodeC11(
  parsedFile: ParsedPythonFile,
  _ctx?: DetectorContext
): Finding[] {
  // If the file has syntax errors, gracefully skip to ensure scanner resilience
  if (parsedFile.hasSyntaxError) {
    return [];
  }

  const lines = parsedFile.content.split(/\r?\n/);

  // 1. Run scope analysis for unused imports (S1)
  const scope = analyzePythonScope(parsedFile.rootNode, parsedFile.path, lines);
  const unusedImportFindings = detectUnusedImports(scope);

  // 2. Run AST traversal for unreachable dead code (S3)
  const unreachableFindings = detectUnreachableCode(
    parsedFile.rootNode,
    parsedFile.path,
    lines
  );

  // Combine and sort findings by starting line
  return [...unusedImportFindings, ...unreachableFindings].sort(
    (a, b) => a.location.startLine - b.location.startLine
  );
}
