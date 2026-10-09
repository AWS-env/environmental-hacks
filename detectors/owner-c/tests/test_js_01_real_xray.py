"""JS-01 end to end with real AWS X-Ray traces of the deployed demo Lambda (fixtures/real/xray, account id scrubbed)."""
import unittest

from helpers import FIXTURES, load_json, run_check
from owner_c.normalize import normalize_all

REAL = FIXTURES / "real" / "xray"
DEMO = (REAL / "demo.js").read_text()
MAPPING = {"owner-c-xray-demo": "demo.js"}


def evaluate(trace_file):
    raw = {"traces": [load_json(REAL / trace_file)], "function_files": MAPPING}
    artifacts = normalize_all({"xray": raw}, [("demo.js", DEMO)])
    return artifacts, run_check("JS-01", {"demo.js": DEMO}, artifacts=artifacts)[1]


class RealXrayJs01Tests(unittest.TestCase):
    def test_serial_trace_confirms_await_in_loop(self):
        artifacts, result = evaluate("serial.trace.json")
        data = artifacts["xray"]["demo.js"]
        self.assertEqual((data["function"], data["max_serial_calls"], data["serial_services"]),
                         ("owner-c-xray-demo", 8, ["Inventory"]))  # subsegments arrive out of time order in the real trace
        self.assertEqual(result["status"], "completed")
        self.assertEqual(len(result["findings"]), 1)

    def test_parallel_trace_is_clean(self):
        artifacts, result = evaluate("parallel.trace.json")
        self.assertEqual(artifacts["xray"]["demo.js"]["max_serial_calls"], 0)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["findings"], [])


if __name__ == "__main__":
    unittest.main()
