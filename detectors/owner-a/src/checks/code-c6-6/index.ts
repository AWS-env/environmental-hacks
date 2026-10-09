import { Finding, DetectorContext } from "../../core/finding.js";
import { ParsedPythonFile } from "../../core/parse.js";
import { detectUnclosedHandles } from "./unclosed-handle.js";

export { detectUnclosedHandles } from "./unclosed-handle.js";

/**
 * Pure function executing the CODE-C6.6 check on an already-parsed Python file AST.
 * Never performs I/O or executes code.
 */
export function checkCodeC66(
  parsedFile: ParsedPythonFile,
  _ctx?: DetectorContext
): Finding[] {
  // If the file has syntax errors, gracefully skip to ensure scanner resilience
  if (parsedFile.hasSyntaxError) return [];

  const lines = parsedFile.content.split(/\r?\n/);
  return detectUnclosedHandles(parsedFile.rootNode, parsedFile.path, lines);
}
