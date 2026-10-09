import { Finding, DetectorContext } from "../../core/finding.js";
import { ParsedPythonFile } from "../../core/parse.js";
import { detectEagerProducers } from "./eager-producers.js";

export { detectEagerProducers } from "./eager-producers.js";

/**
 * Pure function executing the CODE-C3.6 check on an already-parsed Python file AST.
 * Never performs I/O or executes code.
 */
export function checkCodeC36(
  parsedFile: ParsedPythonFile,
  _ctx?: DetectorContext
): Finding[] {
  // If the file has syntax errors, gracefully skip to ensure scanner resilience
  if (parsedFile.hasSyntaxError) {
    return [];
  }

  const lines = parsedFile.content.split(/\r?\n/);
  return detectEagerProducers(parsedFile.rootNode, parsedFile.path, lines);
}
