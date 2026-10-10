import { ArtifactCheckOutcome } from "../../core/artifact.js";
import { checkMemrayStats } from "./memray-stats.js";

export { checkMemrayStats, DEFAULTS as C61_DEFAULTS } from "./memray-stats.js";

/**
 * Pure function executing the CODE-C6.1 artifact check on the normalized `data` of a client-produced
 * `memray stats --json` export. Never performs I/O or executes code.
 */
export function checkCodeC61(data: Record<string, unknown>, context: Record<string, unknown> = {}): ArtifactCheckOutcome {
  return checkMemrayStats(data, context);
}
