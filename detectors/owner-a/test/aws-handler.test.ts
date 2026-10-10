import { describe, it, expect } from "vitest";
import { run, EVENT_SOURCE, DETAIL_TYPE, MAX_DETAIL_BYTES } from "../src/aws/handler.js";
import { staticInput } from "./helpers/contract.js";

const SRC = "import os\nimport json\nprint(os.getcwd())\n";

function fakeClient(opts: { failedEntry?: boolean; busMissing?: boolean } = {}) {
  const calls: { name: string; input: any }[] = [];
  return {
    calls,
    async send(command: any) {
      const name = command.constructor.name;
      calls.push({ name, input: command.input });
      if (name === "DescribeEventBusCommand" && opts.busMissing) throw new Error("ResourceNotFoundException");
      if (name === "PutEventsCommand") {
        return opts.failedEntry
          ? { FailedEntryCount: 1, Entries: [{ ErrorCode: "InternalFailure", ErrorMessage: "boom" }] }
          : { FailedEntryCount: 0, Entries: [{ EventId: "evt-1" }] };
      }
      return {};
    },
  };
}

describe("owner-a Lambda handler core", () => {
  const input = staticInput("CODE-C1.1", { "pkg/a.py": SRC });

  it("describes the bus, then publishes the contract result as detector.result.v1", async () => {
    const client = fakeClient();
    const out = await run(input, client, "findings-hub", true);
    expect(client.calls.map((c) => c.name)).toEqual(["DescribeEventBusCommand", "PutEventsCommand"]);
    const entry = client.calls[1].input.Entries[0];
    expect(entry.Source).toBe(EVENT_SOURCE);
    expect(entry.DetailType).toBe(DETAIL_TYPE);
    const detail = JSON.parse(entry.Detail);
    expect(detail.kind).toBe("result");
    expect(detail.check_id).toBe("CODE-C1.1");
    expect(out).toMatchObject({ check_id: "CODE-C1.1", published: true, event_id: "evt-1" });
    expect(out.findings).toBe(detail.findings.length);
  });

  it("accepts the input wrapped as { input }", async () => {
    const out = await run({ input }, fakeClient(), "findings-hub", true);
    expect(out.published).toBe(true);
  });

  it("does not call AWS when publishing is disabled", async () => {
    const client = fakeClient();
    const out = await run(input, client, "findings-hub", false);
    expect(client.calls).toEqual([]);
    expect(out.published).toBe(false);
  });

  it("evaluates but does not touch AWS when the event carries dry_run: true", async () => {
    const client = fakeClient();
    const out = await run({ input, dry_run: true }, client, "findings-hub", true);
    expect(client.calls).toEqual([]);
    expect(out).toMatchObject({ check_id: "CODE-C1.1", status: "completed", published: false });
    expect(out.findings).toBeGreaterThan(0);
  });

  it("only an exact boolean true triggers a dry run", async () => {
    const client = fakeClient();
    const out = await run({ input, dry_run: "true" }, client, "findings-hub", true);
    expect(out.published).toBe(true);
  });

  it("throws (never reports a clean run) when the bus is missing", async () => {
    await expect(run(input, fakeClient({ busMissing: true }), "findings-hub", true)).rejects.toThrow(/ResourceNotFound/);
  });

  it("throws when PutEvents rejects the entry", async () => {
    await expect(run(input, fakeClient({ failedEntry: true }), "findings-hub", true)).rejects.toThrow(/PutEvents failed: InternalFailure/);
  });

  it("throws when the result is too large to publish inline", async () => {
    const big = staticInput("CODE-C1.1", {
      "pkg/big.py": Array.from({ length: 4000 }, (_, i) => `import mod${i}`).join("\n") + "\n",
    });
    const client = fakeClient();
    await expect(run(big, client, "findings-hub", true)).rejects.toThrow(new RegExp(`limit is ${MAX_DETAIL_BYTES}`));
    expect(client.calls).toEqual([]);
  });

  it("rejects a payload that is not contract v1 input", async () => {
    await expect(run({ nope: true }, fakeClient(), "findings-hub", true)).rejects.toThrow();
  });
});
