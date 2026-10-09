import { Finding, DetectorContext } from "../../core/finding.js";
import { ParsedPythonFile } from "../../core/parse.js";
import { detectMissingEarlyExit } from "./early-exit.js";

export { detectMissingEarlyExit } from "./early-exit.js";

/**
 * Pure function executing the CODE-C3.5 check on an already-parsed Python file AST.
 * Never performs I/O or executes code.
 */
export function checkCodeC35(
  parsedFile: ParsedPythonFile,
  _ctx?: DetectorContext
): Finding[] {
  // If the file has syntax errors, gracefully skip to ensure scanner resilience
  if (parsedFile.hasSyntaxError) {
    return [];
  }

  const lines = parsedFile.content.split(/\r?\n/);
  return detectMissingEarlyExit(parsedFile.rootNode, parsedFile.path, lines);
}
