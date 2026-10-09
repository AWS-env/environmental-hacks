"""X-Ray normalizer: serial runs of same-named sibling subsegments (synthetic documents shaped like real ones)."""
import json
import unittest

from helpers import run_check
from owner_c.normalize import normalize_all

SERIAL_SRC = "async function load(ids) {\n  const out = [];\n  for (const id of ids) {\n    out.push(await inventory.get(id));\n  }\n  return out;\n}\n"


def sub(name, start, end, namespace="remote"):
    return {"id": f"{name}-{start}", "name": name, "namespace": namespace, "start_time": start, "end_time": end}


def trace(children, function="orders-fn", trace_id="1-a"):
    doc = {"id": "seg", "name": function, "origin": "AWS::Lambda::Function", "start_time": 0.0, "end_time": 5.0,
           "subsegments": [{"id": "inv", "name": "Invocation", "start_time": 0.0, "end_time": 5.0, "subsegments": children}]}
    return {"Id": trace_id, "Segments": [{"Id": "seg", "Document": json.dumps(doc)}]}


def raw(*traces, mapping=None):
    return {"traces": list(traces), "function_files": mapping or {"orders-fn": "app.js"}}


def norm(r):
    return normalize_all({"xray": r}, [("app.js", SERIAL_SRC)]).get("xray", {})


class SerialRunTests(unittest.TestCase):
    def test_back_to_back_calls_form_a_run(self):
        data = norm(raw(trace([sub("Inventory", 1.0 + i * 0.2, 1.2 + i * 0.2) for i in range(6)])))["app.js"]
        self.assertEqual((data["profiler"], data["function"], data["max_serial_calls"], data["serial_services"]),
                         ("xray", "orders-fn", 6, ["Inventory"]))
        self.assertAlmostEqual(data["serial_wall_seconds"], 1.2, places=3)
        self.assertEqual(data["traces_analyzed"], 1)

    def test_overlapping_calls_are_parallel_not_serial(self):
        data = norm(raw(trace([sub("Inventory", 1.0, 1.5), sub("Inventory", 1.0, 1.5), sub("Inventory", 1.1, 1.6)])))["app.js"]
        self.assertEqual(data["max_serial_calls"], 0)  # no serial run: only a pair at 1.0/1.0 overlaps, never back-to-back

    def test_clock_jitter_within_epsilon_still_counts_as_serial(self):
        children = [sub("Inventory", 1.0, 1.2), sub("Inventory", 1.1995, 1.4), sub("Inventory", 1.3995, 1.6)]
        self.assertEqual(norm(raw(trace(children)))["app.js"]["max_serial_calls"], 3)

    def test_different_names_break_a_run(self):
        children = [sub("Inventory", 1.0, 1.2), sub("Billing", 1.2, 1.4), sub("Inventory", 1.4, 1.6)]
        self.assertLess(norm(raw(trace(children)))["app.js"]["max_serial_calls"], 2)

    def test_longest_run_across_traces_wins(self):
        short = trace([sub("Inventory", 1.0, 1.2), sub("Inventory", 1.2, 1.4)], trace_id="1-a")
        long = trace([sub("Inventory", 1.0 + i * 0.1, 1.1 + i * 0.1) for i in range(5)], trace_id="1-b")
        data = norm(raw(short, long))["app.js"]
        self.assertEqual((data["max_serial_calls"], data["traces_analyzed"]), (5, 2))

    def test_unmapped_functions_and_files_are_ignored(self):
        children = [sub("Inventory", 1.0 + i * 0.2, 1.2 + i * 0.2) for i in range(4)]
        self.assertEqual(norm(raw(trace(children, function="other-fn"))), {})
        self.assertEqual(norm(raw(trace(children), mapping={"orders-fn": "missing.js"})), {})

    def test_segment_documents_may_already_be_objects(self):
        t = trace([sub("Inventory", 1.0 + i * 0.2, 1.2 + i * 0.2) for i in range(3)])
        t["Segments"][0]["Document"] = json.loads(t["Segments"][0]["Document"])
        self.assertEqual(norm(raw(t))["app.js"]["max_serial_calls"], 3)


class EndToEndTests(unittest.TestCase):
    def test_serial_trace_confirms_the_loop_and_parallel_trace_does_not(self):
        serial = norm(raw(trace([sub("Inventory", 1.0 + i * 0.2, 1.2 + i * 0.2) for i in range(6)])))
        _p, result = run_check("JS-01", {"app.js": SERIAL_SRC}, artifacts={"xray": serial})
        finding = result["findings"][0]
        self.assertEqual((finding["identity"], finding["confidence"]), ("load:await:inventory.get", "medium"))
        self.assertEqual([e["kind"] for e in finding["evidence"]], ["static", "telemetry", "telemetry"])
        self.assertEqual([e["field"] for e in finding["evidence"][1:]], ["max_serial_calls", "serial_wall_seconds"])

        parallel = norm(raw(trace([sub("Inventory", 1.0, 1.4) for _ in range(6)])))
        _p, result = run_check("JS-01", {"app.js": SERIAL_SRC}, artifacts={"xray": parallel})
        self.assertEqual(result["findings"], [])


if __name__ == "__main__":
    unittest.main()
