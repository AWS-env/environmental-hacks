import { Finding, DetectorContext } from "../../core/finding.js";
import { ParsedPythonFile } from "../../core/parse.js";
import { detectRedundantAssignments } from "./redundant-assignment.js";

export { detectRedundantAssignments } from "./redundant-assignment.js";

/**
 * Pure function executing CODE-C1.2 check on an already-parsed Python file AST.
 * Never performs I/O or executes code.
 */
export function checkCodeC12(
  parsedFile: ParsedPythonFile,
  _ctx?: DetectorContext
): Finding[] {
  // If the file has syntax errors, gracefully skip to ensure scanner resilience
  if (parsedFile.hasSyntaxError) {
    return [];
  }

  const lines = parsedFile.content.split(/\r?\n/);
  return detectRedundantAssignments(parsedFile.rootNode, parsedFile.path, lines);
}
