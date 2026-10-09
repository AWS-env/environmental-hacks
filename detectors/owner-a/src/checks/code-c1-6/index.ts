import { Finding, DetectorContext } from "../../core/finding.js";
import { ParsedPythonFile } from "../../core/parse.js";
import { detectUnnecessaryInitialization } from "./unnecessary-initialization.js";

export { detectUnnecessaryInitialization } from "./unnecessary-initialization.js";

/**
 * Pure function executing CODE-C1.6 check on an already-parsed Python file AST.
 * Never performs I/O or executes code.
 */
export function checkCodeC16(
  parsedFile: ParsedPythonFile,
  _ctx?: DetectorContext
): Finding[] {
  // If the file has syntax errors, gracefully skip to ensure scanner resilience
  if (parsedFile.hasSyntaxError) {
    return [];
  }

  const lines = parsedFile.content.split(/\r?\n/);
  return detectUnnecessaryInitialization(parsedFile.rootNode, parsedFile.path, lines);
}
