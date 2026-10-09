import { Finding, DetectorContext } from "../../core/finding.js";
import { ParsedPythonFile } from "../../core/parse.js";
import { detectArrayMutation } from "./mutate-in-loop.js";

export { detectArrayMutation } from "./mutate-in-loop.js";

/**
 * Pure function executing the CODE-C3.7 check on an already-parsed Python file AST.
 * Never performs I/O or executes code.
 */
export function checkCodeC37(
  parsedFile: ParsedPythonFile,
  _ctx?: DetectorContext
): Finding[] {
  // If the file has syntax errors, gracefully skip to ensure scanner resilience
  if (parsedFile.hasSyntaxError) {
    return [];
  }

  const lines = parsedFile.content.split(/\r?\n/);
  return detectArrayMutation(parsedFile.rootNode, parsedFile.path, lines);
}
