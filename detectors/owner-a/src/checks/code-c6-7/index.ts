import { Finding, DetectorContext } from "../../core/finding.js";
import { ParsedPythonFile } from "../../core/parse.js";
import { detectLeakingMutableDefaults } from "./mutable-default.js";

export { detectLeakingMutableDefaults } from "./mutable-default.js";

/**
 * Pure function executing the CODE-C6.7 check on an already-parsed Python file AST.
 * Never performs I/O or executes code.
 */
export function checkCodeC67(
  parsedFile: ParsedPythonFile,
  _ctx?: DetectorContext
): Finding[] {
  // Syntax errors: skip gracefully so the scanner stays resilient.
  if (parsedFile.hasSyntaxError) return [];
  const lines = parsedFile.content.split(/\r?\n/);
  return detectLeakingMutableDefaults(parsedFile.rootNode, parsedFile.path, lines);
}
