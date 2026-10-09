/**
 * Owner-a checks available through the contract adapter (`src/contract.ts`).
 * Each check PR registers itself here with the `detector_version` an input must
 * request and any check-wide limitations the contract result should state.
 */
import { Finding } from "./core/finding.js";
import { ParsedPythonFile } from "./core/parse.js";
import { checkCodeC11 } from "./checks/code-c1-1/index.js";
import { checkCodeC31 } from "./checks/code-c3-1/index.js";

export interface RegisteredCheck {
  version: string;
  run: (parsed: ParsedPythonFile) => Finding[];
  /** Check-wide coverage limitations, stated on every result for this check. */
  limitations: string[];
}

export const CHECKS: ReadonlyMap<string, RegisteredCheck> = new Map([
  [
    "CODE-C1.1",
    {
      version: "0.1.0",
      run: (parsed) => checkCodeC11(parsed),
      limitations: [
        "Python only; JS/TS unused imports are out of scope (bundlers usually elide them).",
      ],
    },
  ],
  [
    "CODE-C3.1",
    {
      version: "0.1.0",
      run: (parsed) => checkCodeC31(parsed),
      limitations: [
        "Static half of R1R2: no profiler evidence that a flagged loop is hot; payoff is engine-dependent (follow-up: OQ-1 profile confirmation).",
      ],
    },
  ],
]);
