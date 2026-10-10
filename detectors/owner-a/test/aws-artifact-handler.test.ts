import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, it, expect } from "vitest";
import { createHash } from "node:crypto";
import { parseArtifactKey, processRecord, evaluateOversize, MAX_ARTIFACT_BYTES, ARTIFACT_EVENT_SOURCE } from "../src/aws/artifact-handler.js";
import { validatePair, validatorAvailable } from "./helpers/contract.js";

const SHA = "a".repeat(40);
const KEY = `artifacts/${encodeURIComponent("github:owner-a-smoke/skeleton")}/scan-1/${SHA}/memray-stats.json`;

function fakeS3(body: Buffer, contentLength?: number) {
  return {
    async send() {
      return { ContentLength: contentLength ?? body.length, Body: { transformToByteArray: async () => new Uint8Array(body) } };
    },
  };
}
function fakeEb() {
  const calls: any[] = [];
  return {
    calls,
    async send(c: any) {
      calls.push(c);
      return c.constructor.name === "PutEventsCommand" ? { FailedEntryCount: 0, Entries: [{ EventId: "evt-9" }] } : {};
    },
  };
}
/** S3 event notifications URL-encode the object key (slashes stay), so a key containing %XX arrives as %25XX. */
const s3EventKey = (key: string) => encodeURIComponent(key).replace(/%2F/g, "/");
const record = (key: string) => ({ s3: { bucket: { name: "b" }, object: { key: s3EventKey(key) } } });

describe("parseArtifactKey", () => {
  it("decodes the repository id and keeps the layout fields", () => {
    expect(parseArtifactKey(KEY)).toEqual({
      repository_id: "github:owner-a-smoke/skeleton",
      scan_id: "scan-1",
      commit_sha: SHA,
      name: "memray-stats.json",
    });
  });
  it.each([
    ["wrong prefix", `other/x/y/${SHA}/n`],
    ["short sha", "artifacts/r/s/abc/n"],
    ["uppercase sha", `artifacts/r/s/${"A".repeat(40)}/n`],
    ["too few parts", "artifacts/r/s"],
  ])("rejects %s", (_n, key) => {
    expect(() => parseArtifactKey(key)).toThrow();
  });
});

describe("processRecord", () => {
  it("publishes an honest unavailable result with the artifact hash, never a clean one", async () => {
    const body = Buffer.from('{"total_allocations": 3}');
    const eb = fakeEb();
    const out = await processRecord(record(KEY), fakeS3(body), eb, "findings-hub");
    expect(out).toMatchObject({ event_id: "evt-9", bytes: body.length });
    const put = eb.calls.find((c) => c.constructor.name === "PutEventsCommand").input.Entries[0];
    expect(put.Source).toBe(ARTIFACT_EVENT_SOURCE);
    const r = JSON.parse(put.Detail);
    expect(r.status).toBe("unavailable");
    expect(r.findings).toEqual([]);
    expect(r.coverage.evaluated_scope).toEqual([]);
    expect(r.coverage.limitations.length).toBeGreaterThan(0);
    expect(r.context.artifact_sha256).toBe(createHash("sha256").update(body).digest("hex"));
    expect(r.scope).toEqual(["artifact:memray-stats.json"]);
  });

  it("a real memray stats export yields a completed result with findings and artifact evidence", async () => {
    const body = readFileSync(join(__dirname, "fixtures", "code-c6-1", "memray-stats-churn.json"));
    const eb = fakeEb();
    const out = await processRecord(record(KEY), fakeS3(body), eb, "findings-hub");
    expect(out).toMatchObject({ status: "completed", findings: 2 });
    const r = JSON.parse(eb.calls.find((c) => c.constructor.name === "PutEventsCommand").input.Entries[0].Detail);
    expect(r.check_id).toBe("CODE-C6.1");
    expect(r.findings[0].evidence.map((e: any) => e.field)).toEqual(["top_allocations_by_count", "total_bytes_allocated", "metadata"]);
  });

  it("a file that is not JSON is unavailable, not clean", async () => {
    const eb = fakeEb();
    const out = await processRecord(record(KEY), fakeS3(Buffer.from("not json at all")), eb, "findings-hub");
    expect(out).toMatchObject({ status: "unavailable", findings: 0 });
  });

  it("decodes the S3 event key encoding once (space arrives as +, a literal + as %2B)", async () => {
    const eventKey = s3EventKey(KEY.replace("memray-stats.json", "x")).replace(/x$/, "my+stats%2B1.json");
    const eb = fakeEb();
    await processRecord({ s3: { bucket: { name: "b" }, object: { key: eventKey } } }, fakeS3(Buffer.from("{}")), eb, "findings-hub");
    const r = JSON.parse(eb.calls.find((c) => c.constructor.name === "PutEventsCommand").input.Entries[0].Detail);
    expect(r.scope).toEqual(["artifact:my stats+1.json"]);
  });

  it("an oversized artifact is an honest unavailable result, decided from ContentLength without reading the body", async () => {
    const eb = fakeEb();
    let bodyRead = false;
    const s3 = {
      async send() {
        return {
          ContentLength: MAX_ARTIFACT_BYTES + 1,
          Body: { transformToByteArray: async () => { bodyRead = true; return new Uint8Array(); } },
        };
      },
    };
    const out = await processRecord(record(KEY), s3, eb, "findings-hub");
    expect(bodyRead).toBe(false);
    expect(out).toMatchObject({ event_id: "evt-9", status: "unavailable", findings: 0, bytes: MAX_ARTIFACT_BYTES + 1 });
    const r = JSON.parse(eb.calls.find((c) => c.constructor.name === "PutEventsCommand").input.Entries[0].Detail);
    expect(r.status).toBe("unavailable");
    expect(r.findings).toEqual([]);
    expect(r.scope).toEqual(["artifact:memray-stats.json"]);
    expect(r.coverage.limitations.join(" ")).toMatch(/limit is 5242880/);
    expect(r.context).toMatchObject({ artifact_bytes: MAX_ARTIFACT_BYTES + 1, too_large: true });
  });

  it.skipIf(!validatorAvailable)("the oversize result passes the shared contract validator", () => {
    const k = parseArtifactKey(KEY);
    const result = evaluateOversize(k, MAX_ARTIFACT_BYTES + 1);
    const input = {
      schema_version: "1.0" as const,
      kind: "input" as const,
      repository_id: k.repository_id,
      scan_id: k.scan_id,
      commit_sha: k.commit_sha,
      check_id: "CODE-C6.1",
      detector_version: "0.1.0",
      context: { parser: "owner-a-profile-parser", artifact_bytes: MAX_ARTIFACT_BYTES + 1, json: false, too_large: true },
      scope: [`artifact:${k.name}`],
      sources: [{ source_id: k.name, scope_id: `artifact:${k.name}`, kind: "artifact" as const, locator: k.name, data: {} }],
    };
    const v = validatePair(input as any, result);
    expect(v.output).not.toMatch(/error|invalid/i);
    expect(v.ok).toBe(true);
  });

  it("an artifact whose ContentLength is missing but whose body is too large is unavailable too", async () => {
    const eb = fakeEb();
    const big = Buffer.alloc(MAX_ARTIFACT_BYTES + 1);
    const s3 = {
      async send() {
        return { Body: { transformToByteArray: async () => new Uint8Array(big) } };
      },
    };
    const out = await processRecord(record(KEY), s3, eb, "findings-hub");
    expect(out).toMatchObject({ status: "unavailable", findings: 0 });
  });

  it("an artifact exactly at the limit is still read and evaluated", async () => {
    const eb = fakeEb();
    const out = await processRecord(record(KEY), fakeS3(Buffer.from("{}"), MAX_ARTIFACT_BYTES), eb, "findings-hub");
    expect(out.status).toBe("unavailable"); // `{}` is not a memray export, but it was read and evaluated
    expect(out.bytes).toBe(2);
  });

  it("a key outside the expected layout is logged and dropped, not thrown (a retry cannot fix it)", async () => {
    const eb = fakeEb();
    const out = await processRecord(record("artifacts/only/two"), fakeS3(Buffer.from("{}")), eb, "findings-hub");
    expect(out).toMatchObject({ status: "ignored", findings: 0 });
    expect(eb.calls).toEqual([]);
  });

  it("transient failures still throw so Lambda retries and the DLQ catches them", async () => {
    const s3 = { async send() { throw new Error("S3 unavailable"); } };
    await expect(processRecord(record(KEY), s3, fakeEb(), "findings-hub")).rejects.toThrow("S3 unavailable");
    const eb = { async send() { return { FailedEntryCount: 1, Entries: [{ ErrorCode: "Throttling", ErrorMessage: "slow down" }] }; } };
    await expect(processRecord(record(KEY), fakeS3(Buffer.from("{}")), eb, "findings-hub")).rejects.toThrow();
  });
});
