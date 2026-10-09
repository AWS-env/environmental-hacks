import { Finding, DetectorContext } from "../../core/finding.js";
import { ParsedPythonFile } from "../../core/parse.js";
import { detectListMembershipInLoop } from "./list-membership.js";

export { detectListMembershipInLoop } from "./list-membership.js";

/**
 * Pure function executing the CODE-C5.1 check on an already-parsed Python file AST.
 * Never performs I/O or executes code.
 */
export function checkCodeC51(
  parsedFile: ParsedPythonFile,
  _ctx?: DetectorContext
): Finding[] {
  // If the file has syntax errors, gracefully skip to ensure scanner resilience
  if (parsedFile.hasSyntaxError) return [];

  const lines = parsedFile.content.split(/\r?\n/);
  return detectListMembershipInLoop(parsedFile.rootNode, parsedFile.path, lines);
}
