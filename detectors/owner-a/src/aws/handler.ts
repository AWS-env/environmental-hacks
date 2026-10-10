/**
 * AWS Lambda entry point for the owner-a static scan (`owner-a-static-scan`).
 *
 * Event: a contract v1 `input` payload, or `{ "input": <contract v1 input> }`.
 * The handler only parses the supplied sources (never imports or executes them),
 * runs `evaluate`, and publishes the contract result to the `findings-hub` bus as
 * `detector.result.v1` (the owner-d writer persists it). It never reports a clean
 * run when publishing failed: any publish problem throws so the invocation fails
 * and lands in the DLQ.
 */
import {
  DescribeEventBusCommand,
  EventBridgeClient,
  PutEventsCommand,
} from "@aws-sdk/client-eventbridge";
import { ContractInputError, ContractResult, evaluate } from "../contract.js";

export const EVENT_SOURCE = "owner-a.static-scan";
export const DETAIL_TYPE = "detector.result.v1";
/** EventBridge rejects entries above 256 KB; stay below it with headroom. */
export const MAX_DETAIL_BYTES = 240 * 1024;

export interface HandlerSummary {
  check_id: string;
  status: ContractResult["status"];
  findings: number;
  published: boolean;
  event_id?: string;
}

export interface EventBridgeLike {
  send(command: DescribeEventBusCommand | PutEventsCommand): Promise<any>;
}

function unwrap(event: unknown): unknown {
  const e = event as { input?: unknown } | null;
  return e && typeof e === "object" && "input" in e && e.input !== undefined ? e.input : event;
}

/** Testable core: `client` and `busName` are injected. */
export async function run(
  event: unknown,
  client: EventBridgeLike,
  busName: string,
  publish: boolean
): Promise<HandlerSummary> {
  const result = evaluate(unwrap(event));
  const summary: HandlerSummary = {
    check_id: result.check_id,
    status: result.status,
    findings: result.findings.length,
    published: false,
  };
  // `{ "input": ..., "dry_run": true }` evaluates and returns the summary without publishing.
  const dryRun = (event as { dry_run?: unknown } | null)?.dry_run === true;
  if (!publish || dryRun) return summary;

  summary.event_id = await publishResult(client, busName, EVENT_SOURCE, result);
  summary.published = true;
  return summary;
}

/** Publish one contract result inline; throws on any problem so nothing is reported as clean. */
export async function publishResult(
  client: EventBridgeLike,
  busName: string,
  source: string,
  result: ContractResult
): Promise<string> {
  const detail = JSON.stringify(result);
  if (Buffer.byteLength(detail, "utf8") > MAX_DETAIL_BYTES) {
    throw new Error(
      `result for ${result.check_id} is ${Buffer.byteLength(detail, "utf8")} bytes; inline publish limit is ${MAX_DETAIL_BYTES}`
    );
  }
  // PutEvents to a missing bus silently drops events, so confirm the bus first.
  await client.send(new DescribeEventBusCommand({ Name: busName }));
  const out = await client.send(
    new PutEventsCommand({
      Entries: [{ EventBusName: busName, Source: source, DetailType: DETAIL_TYPE, Detail: detail }],
    })
  );
  if (out.FailedEntryCount || !out.Entries?.[0]?.EventId) {
    const e = out.Entries?.[0];
    throw new Error(`PutEvents failed: ${e?.ErrorCode ?? "unknown"} ${e?.ErrorMessage ?? ""}`.trim());
  }
  return out.Entries[0].EventId as string;
}

export async function handler(event: unknown): Promise<HandlerSummary> {
  const busName = process.env.FINDINGS_BUS_NAME ?? "findings-hub";
  const publish = (process.env.PUBLISH_RESULTS ?? "true") === "true";
  try {
    return await run(event, new EventBridgeClient({}), busName, publish);
  } catch (err) {
    if (err instanceof ContractInputError) {
      throw new Error(`invalid contract input: ${err.message}`);
    }
    throw err;
  }
}
