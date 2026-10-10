// Bridge from the Python scan runner to the Node-based detectors (owner A, owner B).
// Reads one JSON request on stdin and writes one JSON response on stdout. Source text is only
// parsed by the detectors; it is never executed, imported or evaluated.
//
//   node node_driver.mjs owner-a <detectors/owner-a/dist/index.js> list
//   node node_driver.mjs owner-a <detectors/owner-a/dist/index.js> evaluate  < {base, sources, checks}
//   node node_driver.mjs owner-b <detectors/owner-b/index.js> scan           < {files: [{path, content}]}
//   node node_driver.mjs owner-b <detectors/owner-b/index.js> evaluate       < {inputs: [contract v1 input]}
import { createRequire } from "node:module";
import { pathToFileURL } from "node:url";

const [, , owner, entry, action] = process.argv;

async function readStdin() {
  const chunks = [];
  for await (const chunk of process.stdin) chunks.push(chunk);
  return JSON.parse(Buffer.concat(chunks).toString("utf8"));
}

const message = (error) => (error instanceof Error ? `${error.name}: ${error.message}` : String(error));

async function ownerA() {
  let mod;
  try {
    mod = await import(pathToFileURL(entry).href);
  } catch (error) {
    return { unavailable: `owner A build could not be loaded (${message(error)})` };
  }
  if (action === "list") {
    return { checks: [...mod.CHECKS.entries()].map(([check_id, check]) => ({ check_id, version: check.version })) };
  }
  const { base, sources, checks } = await readStdin();
  const scope = [...new Set(sources.map((s) => s.scope_id))];
  return {
    results: checks.map(({ check_id, detector_version }) => {
      try {
        return { check_id, result: mod.evaluate({ ...base, check_id, detector_version, scope, sources }) };
      } catch (error) {
        return { check_id, error: message(error) };
      }
    }),
  };
}

function ownerB() {
  const require = createRequire(entry);
  let detector, Engine;
  try {
    detector = require(entry);
    Engine = require("php-parser");
  } catch (error) {
    return { unavailable: `owner B could not be loaded; run \`npm ci\` at the repository root (${message(error)})` };
  }
  if (action === "list") {
    return { checks: Object.keys(detector.checks || {}).sort() };
  }
  if (action === "evaluate") {
    // Contract v1 inputs (static ORM checks); evaluate() may return a Promise. A crash is that check's error.
    return readStdin().then(async ({ inputs }) => ({
      results: await Promise.all(inputs.map(async (input) => {
        try {
          return { check_id: input.check_id, result: await detector.evaluate(input) };
        } catch (error) {
          return { check_id: input.check_id, error: message(error) };
        }
      })),
    }));
  }
  // The legacy scanSource() suppresses parse errors, so a strict parse decides whether a file
  // was really evaluated; without it an unparseable file would look clean.
  const strict = new Engine({ parser: { extractDoc: false, suppressErrors: false }, ast: { withPositions: false } });
  return readStdin().then(({ files }) => ({
    files: files.map(({ path, content }) => {
      try {
        strict.parseCode(content, path);
      } catch (error) {
        return { path, parsed: false, reason: `could not be parsed (${message(error).slice(0, 160)})` };
      }
      try {
        return { path, parsed: true, findings: detector.scanSource(content, path) };
      } catch (error) {
        return { path, parsed: false, reason: `scanSource failed (${message(error).slice(0, 160)})` };
      }
    }),
  }));
}

const handlers = { "owner-a": ownerA, "owner-b": ownerB };
if (!handlers[owner]) {
  process.stderr.write(`unknown owner ${owner}\n`);
  process.exit(2);
}
process.stdout.write(JSON.stringify(await handlers[owner]()));
