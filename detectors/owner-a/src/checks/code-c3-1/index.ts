import { Finding, DetectorContext } from "../../core/finding.js";
import { ParsedPythonFile } from "../../core/parse.js";
import { detectInefficientIteration } from "./iteration-construct.js";

export { detectInefficientIteration } from "./iteration-construct.js";

/**
 * Pure function executing the CODE-C3.1 check on an already-parsed Python file AST.
 * Never performs I/O or executes code.
 */
export function checkCodeC31(
  parsedFile: ParsedPythonFile,
  _ctx?: DetectorContext
): Finding[] {
  // If the file has syntax errors, gracefully skip to ensure scanner resilience
  if (parsedFile.hasSyntaxError) {
    return [];
  }

  const lines = parsedFile.content.split(/\r?\n/);

  return detectInefficientIteration(parsedFile.rootNode, parsedFile.path, lines);
}
