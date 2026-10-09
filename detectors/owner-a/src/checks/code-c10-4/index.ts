import { Finding, DetectorContext } from "../../core/finding.js";
import { ParsedPythonFile } from "../../core/parse.js";
import { detectStringConcatInLoop } from "./string-concat.js";

export { detectStringConcatInLoop } from "./string-concat.js";

/**
 * Pure function executing the CODE-C10.4 check on an already-parsed Python file AST.
 * Never performs I/O or executes code.
 */
export function checkCodeC104(
  parsedFile: ParsedPythonFile,
  _ctx?: DetectorContext
): Finding[] {
  if (parsedFile.hasSyntaxError) return [];
  const lines = parsedFile.content.split(/\r?\n/);
  return detectStringConcatInLoop(parsedFile.rootNode, parsedFile.path, lines);
}
