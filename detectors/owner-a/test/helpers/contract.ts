import { spawnSync } from "node:child_process";
import { mkdtempSync, writeFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import type { ContractInput, ContractResult } from "../../src/contract.js";

/** Repository root (holds `shared/contracts/`). */
export const REPO_ROOT = join(__dirname, "..", "..", "..", "..");

const PYTHON = process.env.CONTRACT_PYTHON ?? "python3";

/**
 * True when the shared Python validator can run (Python 3.12+ with
 * `shared/contracts/requirements.txt`). CI sets REQUIRE_CONTRACT_VALIDATOR=1 so
 * a missing validator fails instead of skipping.
 */
export const validatorAvailable: boolean = (() => {
  const probe = spawnSync(PYTHON, ["-c", "import shared.contracts.validation"], {
    cwd: REPO_ROOT,
    encoding: "utf8",
  });
  const ok = probe.status === 0;
  if (!ok && process.env.REQUIRE_CONTRACT_VALIDATOR === "1") {
    throw new Error(
      `Shared contract validator unavailable via '${PYTHON}': ${probe.stderr || probe.error?.message}`
    );
  }
  return ok;
})();

/** Run `python -m shared.contracts.validation <result> --input <input>`. */
export function validatePair(
  input: ContractInput,
  result: ContractResult
): { ok: boolean; output: string } {
  const dir = mkdtempSync(join(tmpdir(), "owner-a-contract-"));
  try {
    const inputPath = join(dir, "input.json");
    const resultPath = join(dir, "result.json");
    writeFileSync(inputPath, JSON.stringify(input));
    writeFileSync(resultPath, JSON.stringify(result));
    const run = spawnSync(
      PYTHON,
      ["-m", "shared.contracts.validation", resultPath, "--input", inputPath],
      { cwd: REPO_ROOT, encoding: "utf8" }
    );
    return { ok: run.status === 0, output: `${run.stdout}${run.stderr}`.trim() };
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
}

/** Build a contract v1 input with one static Python source per file. */
export function staticInput(
  checkId: string,
  files: Record<string, string>,
  overrides: Partial<ContractInput> = {}
): ContractInput {
  const scope = Object.keys(files).map((path) => `file:${path}`);
  return {
    schema_version: "1.0",
    kind: "input",
    repository_id: "github:AWS-env/environmental-hacks-fixtures",
    scan_id: "owner-a-fixture-scan",
    commit_sha: "0123456789abcdef0123456789abcdef01234567",
    check_id: checkId,
    detector_version: "0.1.0",
    context: { language: "python", synthetic_fixture: true },
    scope,
    sources: Object.entries(files).map(([path, content]) => ({
      source_id: path,
      scope_id: `file:${path}`,
      kind: "static" as const,
      locator: path,
      content,
    })),
    ...overrides,
  };
}
