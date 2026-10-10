/**
 * Owner-a checks available through the contract adapter (`src/contract.ts`).
 * Each check PR registers itself here with the `detector_version` an input must
 * request and any check-wide limitations the contract result should state.
 */
import { Finding } from "./core/finding.js";
import { ParsedPythonFile } from "./core/parse.js";
import { checkCodeC11 } from "./checks/code-c1-1/index.js";
import { checkCodeC12 } from "./checks/code-c1-2/index.js";
import { checkCodeC31 } from "./checks/code-c3-1/index.js";
import { checkCodeC32 } from "./checks/code-c3-2/index.js";
import { checkCodeC33 } from "./checks/code-c3-3/index.js";
import { checkCodeC16 } from "./checks/code-c1-6/index.js";
import { checkCodeC37 } from "./checks/code-c3-7/index.js";
import { checkCodeC35 } from "./checks/code-c3-5/index.js";
import { checkCodeC36 } from "./checks/code-c3-6/index.js";
import { checkCodeC13 } from "./checks/code-c1-3/index.js";
import { checkCodeC51 } from "./checks/code-c5-1/index.js";
import { checkCodeC104 } from "./checks/code-c10-4/index.js";
import { checkCodeC67 } from "./checks/code-c6-7/index.js";
import { checkCodeC66 } from "./checks/code-c6-6/index.js";

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
    "CODE-C1.2",
    {
      version: "0.1.0",
      run: (parsed) => checkCodeC12(parsed),
      limitations: [
        "Dead stores are found for function locals in straight-line code only: module/class stores and overwrites across branches or early exits (CODE-C1.6) are not analysed.",
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
  [
    "CODE-C3.2",
    {
      version: "0.1.0",
      run: (parsed) => checkCodeC32(parsed),
      limitations: [
        "Callee purity is not verified statically: hoisting a flagged call is safe only if it is side-effect free.",
      ],
    },
  ],
  [
    "CODE-C3.3",
    {
      version: "0.1.0",
      run: (parsed) => checkCodeC33(parsed),
      limitations: [
        "Setup cost and freshness are not verified statically: hoist only objects that are safe to share across iterations.",
      ],
    },
  ],
  [
    "CODE-C1.6",
    {
      version: "0.1.0",
      run: (parsed) => checkCodeC16(parsed),
      limitations: [
        "Function-local `name = <allocation or call>` only; constants, module/class bodies, `try`/`with` blocks and partial overwrites (no `else`) are not analysed.",
      ],
    },
  ],
  [
    "CODE-C3.7",
    {
      version: "0.1.0",
      run: (parsed) => checkCodeC37(parsed),
      limitations: [
        "Container type is inferred only from bindings visible in the file; a deque passed in from elsewhere looks like a list.",
      ],
    },
  ],
  [
    "CODE-C3.5",
    {
      version: "0.1.0",
      run: (parsed) => checkCodeC35(parsed),
      limitations: [
        "Match position is not measured: an early exit saves work only when the match lands early.",
      ],
    },
  ],
  [
    "CODE-C3.6",
    {
      version: "0.1.0",
      run: (parsed) => checkCodeC36(parsed),
      limitations: [
        "Consumed share is not measured: a lazy producer saves work only for the elements its consumer never reads.",
      ],
    },
  ],
  [
    "CODE-C1.3",
    {
      version: "0.1.0",
      run: (parsed) => checkCodeC13(parsed),
      limitations: [
        "Only `if`/`elif`/`else` chains and conditional expressions are analysed; `match` statements and zero-cost jumps (a trailing `continue`/`return`, `else: pass`) are not flagged.",
      ],
    },
  ],
  [
    "CODE-C5.1",
    {
      version: "0.1.0",
      run: (parsed) => checkCodeC51(parsed),
      limitations: [
        "Collection binding is resolved only inside the file; a list-typed name that arrives from another module is not seen, and list size and trip count are not measured.",
      ],
    },
  ],
  [
    "CODE-C10.4",
    {
      version: "0.1.0",
      run: (parsed) => checkCodeC104(parsed),
      limitations: [
        "Trip count is not measured, and CPython can sometimes resize a local string in place, so the quadratic cost is not guaranteed.",
      ],
    },
  ],
  [
    "CODE-C6.7",
    {
      version: "0.1.0",
      run: (parsed) => checkCodeC67(parsed),
      limitations: [
        "Only defaults that the function body visibly mutates are reported; mutation through aliases or helper calls is not tracked.",
      ],
    },
  ],
  [
    "CODE-C6.6",
    {
      version: "0.1.0",
      run: (parsed) => checkCodeC66(parsed),
      limitations: [
        "Reliability finding with low energy weight: handles that are stored, passed on or returned are treated as owned elsewhere, and CPython refcounting often closes a dropped file immediately.",
      ],
    },
  ],
]);
