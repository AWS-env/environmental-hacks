// Heap collector for owner C's JS detectors (JS-03, JS-05, JS-07).
//
//   node -r ./collect-heap.js app.js          -> writes collected.heapprofile (or $HEAP_OUT)
//
// V8's default sampling heap profile (node --heap-prof) only reports objects that are still alive when
// it stops, which hides temporary arrays, JSON clones and accumulator copies. This preload starts the
// inspector's sampler with includeObjectsCollectedByMajorGC / MinorGC so freed objects are counted too.
// It only observes the process; it does not change application behaviour. Upload the file as the
// `heapprofile` artifact.
const inspector = require('node:inspector');
const fs = require('node:fs');

const session = new inspector.Session();
session.connect();
session.post('HeapProfiler.enable');
session.post('HeapProfiler.startSampling', {
  samplingInterval: 8192,
  includeObjectsCollectedByMajorGC: true,
  includeObjectsCollectedByMinorGC: true,
});

process.on('exit', () => {
  session.post('HeapProfiler.stopSampling', (err, res) => {
    if (err) {
      console.error('heap collector failed', err);
      return;
    }
    fs.writeFileSync(process.env.HEAP_OUT || 'collected.heapprofile', JSON.stringify(res.profile));
  });
});
