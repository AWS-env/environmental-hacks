import { ArtifactCheckOutcome, ArtifactConfidence, ArtifactFinding } from "../../core/artifact.js";

/**
 * CODE-C6.1 Unnecessary object, runtime half: allocation churn read from a client-produced
 * `memray stats --json` export (client CI runs `memray run` and `memray stats --json`; we only parse).
 *
 * Signal: the run allocated far more bytes in total than its peak (`total_bytes_allocated` /
 * `metadata.peak_memory`), i.e. memory is allocated and released repeatedly instead of held, and a
 * client-code location accounts for a large share of the allocation count. Field names come from a real
 * `memray stats --json` export (memray 1.x, Python 3.13), checked in as test/fixtures/code-c6-1/.
 */

const REFERENCES = [
  "https://bloomberg.github.io/memray/stats.html",
  "https://bloomberg.github.io/memray/temporary_allocations.html",
  "https://arxiv.org/abs/2604.04809",
  "https://github.com/AWS-env/environmental-hacks/issues/79",
];

export const DEFAULTS = {
  /** Ignore runs that allocated less than this in total (bytes); too small to mean anything. */
  min_total_bytes: 1_048_576,
  /** total_bytes_allocated / peak_memory at or above this counts as churn. */
  churn_ratio_min: 20,
  /** A hotspot needs at least this many allocations. */
  min_hotspot_count: 1000,
  /** ...and at least this share of all allocations in the run. */
  min_hotspot_share: 0.05,
};

/** Locations that are runtime or third-party plumbing, not the client's own code. */
const NON_CLIENT = /(^|:)(<frozen |<root>|<string>|<unknown>)|site-packages|dist-packages|\/lib\/python\d|\\Lib\\|\/usr\/(local\/)?lib\//;

interface Hotspot {
  location: string;
  count: number;
}

const isObject = (v: unknown): v is Record<string, unknown> => typeof v === "object" && v !== null && !Array.isArray(v);
const isCount = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v) && v >= 0;

function numberSetting(context: Record<string, unknown>, key: keyof typeof DEFAULTS): number {
  const v = context[key];
  return typeof v === "number" && Number.isFinite(v) && v >= 0 ? v : DEFAULTS[key];
}

/** `func:file.py:12` -> `func:file.py` (identity must not contain line numbers). */
function lineFree(location: string): string {
  return location.replace(/:\d+$/, "");
}

/** Decimal units, matching memray's own `stats` output (27424 kB -> "27.4 MB"). */
function human(bytes: number): string {
  if (bytes >= 1_000_000) return `${(bytes / 1_000_000).toFixed(1)} MB`;
  if (bytes >= 1_000) return `${(bytes / 1_000).toFixed(1)} kB`;
  return `${bytes} B`;
}

export function checkMemrayStats(data: Record<string, unknown>, context: Record<string, unknown> = {}): ArtifactCheckOutcome {
  const totalBytes = data.total_bytes_allocated;
  const totalAllocs = data.total_num_allocations;
  const metadata = data.metadata;
  const peak = isObject(metadata) ? metadata.peak_memory : undefined;
  const byCount = data.top_allocations_by_count;

  if (!isCount(totalBytes) || !isCount(totalAllocs) || !isCount(peak) || !Array.isArray(byCount)) {
    return {
      kind: "unavailable",
      reason:
        "not a memray stats JSON export this parser understands (needs total_bytes_allocated, total_num_allocations, metadata.peak_memory and top_allocations_by_count).",
    };
  }
  const hotspots: Hotspot[] = [];
  for (const entry of byCount) {
    if (!isObject(entry) || typeof entry.location !== "string" || !isCount(entry.count)) {
      return { kind: "unavailable", reason: "top_allocations_by_count has an entry without a string location and numeric count." };
    }
    hotspots.push({ location: entry.location, count: entry.count });
  }

  const minTotal = numberSetting(context, "min_total_bytes");
  const churnMin = numberSetting(context, "churn_ratio_min");
  const minCount = numberSetting(context, "min_hotspot_count");
  const minShare = numberSetting(context, "min_hotspot_share");

  // Too little data or no peak to compare against: a valid artifact with nothing to report, not an error.
  if (totalBytes < minTotal || peak <= 0 || totalAllocs <= 0) return { kind: "evaluated", findings: [] };
  const churn = totalBytes / peak;
  if (churn < churnMin) return { kind: "evaluated", findings: [] };

  const findings: ArtifactFinding[] = [];
  const ordinals = new Map<string, number>();
  const ranked = [...hotspots].sort((a, b) => b.count - a.count);
  for (const h of ranked) {
    if (NON_CLIENT.test(h.location)) continue;
    const share = h.count / totalAllocs;
    if (h.count < minCount || share < minShare) continue;

    const anchor = lineFree(h.location);
    const n = (ordinals.get(anchor) ?? 0) + 1;
    ordinals.set(anchor, n);
    const confidence: ArtifactConfidence = share >= 0.5 && churn >= churnMin * 5 ? "medium" : "low";
    findings.push({
      identity: `allocation-churn:${anchor}#${n}`,
      summary:
        `${h.location} made ${h.count} allocations (${(share * 100).toFixed(0)}% of all ${totalAllocs}); the run allocated ` +
        `${human(totalBytes)} in total against a ${human(peak)} peak (${churn.toFixed(0)}x), so memory is allocated and released ` +
        `repeatedly instead of held, which points to short-lived temporary objects at this location.`,
      confidence,
      recommendation:
        `Look at ${h.location}: avoid building intermediate lists/tuples/strings per call (iterate lazily, reuse a buffer, or ` +
        `hoist the allocation out of the hot path), then re-run memray to confirm the allocation count drops.`,
      references: REFERENCES,
      fields: ["top_allocations_by_count", "total_bytes_allocated", "metadata"],
    });
  }
  return { kind: "evaluated", findings };
}
