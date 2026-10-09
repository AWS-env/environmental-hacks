// Demo workload for the JS-01 X-Ray route (not detector code). Deployed as `owner-c-xray-demo` with Active tracing.
//
// `loadSerial` awaits each downstream call inside a loop (JS-01), `loadParallel` starts them together.
// Each simulated downstream call is recorded as an X-Ray subsegment named "Inventory", so the traces show
// back-to-back calls for the serial mode and overlapping calls for the parallel mode.
const AWSXRay = require('aws-xray-sdk-core');

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function inventoryGet(id) {
  const subsegment = AWSXRay.getSegment().addNewSubsegment('Inventory');
  try {
    await sleep(80);
    return id;
  } finally {
    subsegment.close();
  }
}

async function loadSerial(ids) {
  const out = [];
  for (const id of ids) {
    out.push(await inventoryGet(id));
  }
  return out;
}

async function loadParallel(ids) {
  return Promise.all(ids.map((id) => inventoryGet(id)));
}

exports.handler = async (event = {}) => {
  const ids = Array.from({ length: event.count ?? 8 }, (_, i) => i);
  const started = Date.now();
  const items = event.mode === 'parallel' ? await loadParallel(ids) : await loadSerial(ids);
  return { mode: event.mode === 'parallel' ? 'parallel' : 'serial', items: items.length, ms: Date.now() - started };
};
