/**
 * AWS Lambda entry point for client-uploaded profiler artifacts (`owner-a-profile-parser`).
 *
 * Trigger: S3 ObjectCreated on `artifacts/<repository_id>/<scan_id>/<commit_sha>/<name>` where
 * `<repository_id>` is URL-encoded. The artifact is only read as bytes (size-capped) and hashed;
 * client code is never executed. SKELETON: the real memray/cProfile export parsers arrive with
 * their checks, so this version publishes an honest `unavailable` result (never "clean") that
 * proves the upload -> trigger -> parse -> publish path end to end.
 */
import { createHash } from "node:crypto";
import { GetObjectCommand, HeadObjectCommand, S3Client } from "@aws-sdk/client-s3";
import { EventBridgeClient } from "@aws-sdk/client-eventbridge";
import { ContractResult, evaluate } from "../contract.js";
import { EventBridgeLike, publishResult } from "./handler.js";

export const ARTIFACT_EVENT_SOURCE = "owner-a.profile-parser";
export const MAX_ARTIFACT_BYTES = 5 * 1024 * 1024;
const PARSER_CHECK_ID = "CODE-C6.1";
const PARSER_VERSION = "0.1.0";

export interface ArtifactKey {
  repository_id: string;
  scan_id: string;
  commit_sha: string;
  name: string;
}

export interface S3Like {
  send(command: GetObjectCommand | HeadObjectCommand): Promise<any>;
}

/** `artifacts/<url-encoded repository_id>/<scan_id>/<40-hex commit_sha>/<name>`; throws on anything else. */
export function parseArtifactKey(key: string): ArtifactKey {
  const parts = key.split("/");
  if (parts.length !== 5 || parts[0] !== "artifacts") throw new Error(`unexpected artifact key layout: ${key}`);
  const [, repo, scan, sha, name] = parts;
  if (!/^[0-9a-f]{40}$/.test(sha)) throw new Error("commit_sha segment must be a full lowercase 40-hex sha");
  if (!scan || !name) throw new Error("scan_id and artifact name must be nonempty");
  return { repository_id: decodeURIComponent(repo), scan_id: scan, commit_sha: sha, name };
}

/**
 * Evaluate an uploaded artifact with the CODE-C6.1 memray-stats check. Anything that is not a JSON object
 * (or not a memray stats export) comes back as an honest `unavailable` result from `evaluate`, never "clean".
 */
export function evaluateArtifact(k: ArtifactKey, body: Buffer, sha256: string): ContractResult {
  let data: Record<string, unknown> = {};
  let parsedJson = true;
  try {
    const v = JSON.parse(body.toString("utf8"));
    if (v && typeof v === "object" && !Array.isArray(v)) data = v;
    else parsedJson = false;
  } catch {
    parsedJson = false;
  }
  return evaluateParsed(k, data, { artifact_sha256: sha256, artifact_bytes: body.length, json: parsedJson });
}

/**
 * An artifact over the size limit is never read: the result is an honest `unavailable` that says why, so the
 * client sees the upload was not evaluated instead of the Lambda failing, retrying and filling the DLQ.
 */
export function evaluateOversize(k: ArtifactKey, bytes: number): ContractResult {
  const result = evaluateParsed(k, {}, { artifact_bytes: bytes, json: false, too_large: true });
  result.coverage.limitations.push(`artifact is ${bytes} bytes; limit is ${MAX_ARTIFACT_BYTES}; it was not read.`);
  return result;
}

function evaluateParsed(k: ArtifactKey, data: Record<string, unknown>, context: Record<string, unknown>): ContractResult {
  const scopeId = `artifact:${k.name}`;
  return evaluate({
    schema_version: "1.0",
    kind: "input",
    repository_id: k.repository_id,
    scan_id: k.scan_id,
    commit_sha: k.commit_sha,
    check_id: PARSER_CHECK_ID,
    detector_version: PARSER_VERSION,
    context: { parser: "owner-a-profile-parser", ...context },
    scope: [scopeId],
    sources: [{ source_id: k.name, scope_id: scopeId, kind: "artifact", locator: k.name, data }],
  });
}

export interface ProcessedRecord {
  key: string;
  event_id: string;
  bytes: number;
  status: string;
  findings: number;
}

/**
 * Problems a retry cannot fix (a key outside the layout, an oversized object) are answered or dropped here;
 * transient failures (S3, EventBridge) throw so Lambda retries and the dead-letter queue catches the rest.
 */
export async function processRecord(
  record: { s3: { bucket: { name: string }; object: { key: string } } },
  s3: S3Like,
  eb: EventBridgeLike,
  busName: string
): Promise<ProcessedRecord> {
  const key = decodeURIComponent(record.s3.object.key.replace(/\+/g, " "));
  let parsed: ArtifactKey;
  try {
    parsed = parseArtifactKey(key);
  } catch (error) {
    // No repository or scan can be named, so there is nothing to publish a result against.
    console.warn(JSON.stringify({ level: "warn", message: "artifact key ignored", key, error: String(error) }));
    return { key, event_id: "", bytes: 0, status: "ignored", findings: 0 };
  }
  const head = await s3.send(new HeadObjectCommand({ Bucket: record.s3.bucket.name, Key: key }));
  if (head.ContentLength !== undefined && head.ContentLength > MAX_ARTIFACT_BYTES) {
    return publishProcessed(key, evaluateOversize(parsed, head.ContentLength), head.ContentLength, eb, busName);
  }
  const obj = await s3.send(new GetObjectCommand({ Bucket: record.s3.bucket.name, Key: key }));
  const body = Buffer.from(await obj.Body.transformToByteArray());
  if (body.length > MAX_ARTIFACT_BYTES) {
    return publishProcessed(key, evaluateOversize(parsed, body.length), body.length, eb, busName);
  }
  const sha256 = createHash("sha256").update(body).digest("hex");
  return publishProcessed(key, evaluateArtifact(parsed, body, sha256), body.length, eb, busName);
}

async function publishProcessed(
  key: string,
  result: ContractResult,
  bytes: number,
  eb: EventBridgeLike,
  busName: string
): Promise<ProcessedRecord> {
  const event_id = await publishResult(eb, busName, ARTIFACT_EVENT_SOURCE, result);
  return { key, event_id, bytes, status: result.status, findings: result.findings.length };
}

export async function handler(event: { Records?: any[] }) {
  const busName = process.env.FINDINGS_BUS_NAME ?? "findings-hub";
  const s3 = new S3Client({});
  const eb = new EventBridgeClient({});
  const out = [];
  for (const record of event.Records ?? []) out.push(await processRecord(record, s3, eb, busName));
  return out;
}
