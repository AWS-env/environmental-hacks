import { Finding, DetectorContext } from "../../core/finding.js";
import { ParsedPythonFile } from "../../core/parse.js";
import { detectRedundantControlFlow } from "./redundant-control-flow.js";

export { detectRedundantControlFlow } from "./redundant-control-flow.js";

/**
 * Pure function executing CODE-C1.3 check on an already-parsed Python file AST.
 * Never performs I/O or executes code.
 */
export function checkCodeC13(
  parsedFile: ParsedPythonFile,
  _ctx?: DetectorContext
): Finding[] {
  // If the file has syntax errors, gracefully skip to ensure scanner resilience
  if (parsedFile.hasSyntaxError) {
    return [];
  }

  const lines = parsedFile.content.split(/\r?\n/);
  return detectRedundantControlFlow(parsedFile.rootNode, parsedFile.path, lines);
}
