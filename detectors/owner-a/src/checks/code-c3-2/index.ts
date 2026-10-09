import { Finding, DetectorContext } from "../../core/finding.js";
import { ParsedPythonFile } from "../../core/parse.js";
import { detectLoopInvariants } from "./invariant.js";

export { detectLoopInvariants } from "./invariant.js";

/**
 * Pure function executing CODE-C3.2 check on an already-parsed Python file AST.
 * Never performs I/O or executes code.
 */
export function checkCodeC32(
  parsedFile: ParsedPythonFile,
  _ctx?: DetectorContext
): Finding[] {
  // If the file has syntax errors, gracefully skip to ensure scanner resilience
  if (parsedFile.hasSyntaxError) {
    return [];
  }

  const lines = parsedFile.content.split(/\r?\n/);
  return detectLoopInvariants(parsedFile.rootNode, parsedFile.path, lines);
}
