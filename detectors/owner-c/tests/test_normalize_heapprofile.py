"""Real artifact: V8 heap profile recorded from fixtures/real/node/app.js with Node 24.

Heap: node --no-opt -r collectors/collect-heap.js app.js   (includes objects freed by GC)
"""
import json
import unittest

from helpers import FIXTURES, run_check
from owner_c.normalize import normalize_all

NODE = FIXTURES / "real" / "node"
APP = (NODE / "app.js").read_text()
HEAP = json.loads((NODE / "app.heapprofile").read_text())


def line_of(text):
    return next(i for i, line in enumerate(APP.splitlines(), 1) if text in line)


class HeapProfileTests(unittest.TestCase):
    def test_builtin_allocations_are_credited_to_the_js_caller(self):
        data = normalize_all({"heapprofile": HEAP}, [("app.js", APP)])["node-heap"]["app.js"]
        # arrays built by the native filter/map are credited to `names`, not dropped
        self.assertGreater(data[f"allocated_bytes_function_line_{line_of('function names')}"], 10 * 1024 * 1024)

    def test_end_to_end_confirmations(self):
        artifacts = normalize_all({"heapprofile": HEAP}, [("app.js", APP)])
        expected = {
            "JS-03": ("cloneRows.<anonymous>:JSON.parse(JSON.stringify)", line_of("JSON.stringify")),
            "JS-05": ("names:filter.map.map", line_of("rows.filter")),
        }
        for check, (identity, line) in expected.items():
            with self.subTest(check=check):
                _payload, result = run_check(check, {"app.js": APP}, artifacts=artifacts)
                self.assertEqual([(f["identity"], f["evidence"][0]["line_start"]) for f in result["findings"]],
                                 [(identity, line)])
                self.assertTrue(result["findings"][0]["evidence"][1]["field"].startswith("allocated_bytes_function_line_"))


if __name__ == "__main__":
    unittest.main()
