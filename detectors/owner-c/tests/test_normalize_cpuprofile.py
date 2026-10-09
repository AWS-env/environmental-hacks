"""Real artifact: V8 CPU profile recorded from fixtures/real/node/app.js with Node 24 (`node --no-opt --cpu-prof app.js`)."""
import json
import unittest

from helpers import FIXTURES, run_check
from owner_c.normalize import normalize_all
from owner_c.normalize.cpuprofile import normalize as normalize_cpu

NODE = FIXTURES / "real" / "node"
APP = (NODE / "app.js").read_text()
CPU = json.loads((NODE / "app.cpuprofile").read_text())


def line_of(text):
    return next(i for i, line in enumerate(APP.splitlines(), 1) if text in line)


class CpuProfileTests(unittest.TestCase):
    def setUp(self):
        self.data = normalize_all({"cpuprofile": CPU}, [("app.js", APP)])["node-cpu"]["app.js"]

    def test_function_shares_are_keyed_by_start_line(self):
        self.assertEqual(self.data["profiler"], "node-cpu")
        for fn in ("function lookup", "function readSelf", "function format"):
            self.assertGreater(self.data[f"function_time_share_line_{line_of(fn)}"], 0.05, fn)

    def test_module_wrapper_frame_is_not_a_function(self):
        # app.js line 1 is `const fs = ...`: the top-level frame starts at 0:0 and holds ~98% of samples
        self.assertNotIn("function_time_share_line_1", self.data)

    def test_module_frame_never_inflates_a_function_starting_on_line_one(self):
        profile = {"startTime": 0, "endTime": 10, "samples": [2, 2, 2, 3], "timeDeltas": [1, 1, 1, 1], "nodes": [
            {"id": 1, "callFrame": {"functionName": "(root)", "url": "", "lineNumber": -1, "columnNumber": -1}, "children": [2]},
            {"id": 2, "callFrame": {"functionName": "", "url": "file:///w/a.js", "lineNumber": 0, "columnNumber": 0},
             "hitCount": 3, "children": [3]},
            {"id": 3, "callFrame": {"functionName": "f", "url": "file:///w/a.js", "lineNumber": 0, "columnNumber": 0},
             "hitCount": 1},
        ]}
        data = normalize_cpu(profile, [("a.js", "function f() {}\n")])["a.js"]
        self.assertEqual(data["function_time_share_line_1"], 0.25)  # only f's own sample, not the module's

    def test_end_to_end_confirmations(self):
        artifacts = normalize_all({"cpuprofile": CPU}, [("app.js", APP)])
        expected = {
            "JS-02": ("lookup:items.includes", line_of("items.includes")),
            "JS-04": ("readSelf:fs.readFileSync", line_of("fs.readFileSync")),
            "JS-08": ("format:new Intl.NumberFormat", line_of("new Intl.NumberFormat")),
        }
        for check, (identity, line) in expected.items():
            with self.subTest(check=check):
                _payload, result = run_check(check, {"app.js": APP}, artifacts=artifacts)
                self.assertEqual([(f["identity"], f["evidence"][0]["line_start"]) for f in result["findings"]],
                                 [(identity, line)])
                self.assertTrue(result["findings"][0]["evidence"][1]["field"].startswith("function_time_share_line_"))


if __name__ == "__main__":
    unittest.main()
