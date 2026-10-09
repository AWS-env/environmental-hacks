import { Finding, DetectorContext } from "../../core/finding.js";
import { ParsedPythonFile } from "../../core/parse.js";
import { detectBlockingCallInAsync } from "./blocking-call.js";

export { detectBlockingCallInAsync } from "./blocking-call.js";

/**
 * Pure function executing the CODE-C11.4 check on an already-parsed Python file AST.
 * Never performs I/O or executes code.
 */
export function checkCodeC114(
  parsedFile: ParsedPythonFile,
  _ctx?: DetectorContext
): Finding[] {
  // If the file has syntax errors, gracefully skip to ensure scanner resilience
  if (parsedFile.hasSyntaxError) {
    return [];
  }

  const lines = parsedFile.content.split(/\r?\n/);
  return detectBlockingCallInAsync(parsedFile.rootNode, parsedFile.path, lines);
}