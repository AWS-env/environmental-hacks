"""Owner B Python EXPLAIN collector without a database: safety rules, dedupe, redaction, and the path
collector -> contract input -> detectors (real PostgreSQL 16 plans from detectors/owner-b/test/fixtures/explain).

Lives in tests/scanner because CI already runs that directory; the collector itself ships in detectors/owner-b/client-collectors.
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

from scanner.adapters import node
from scanner.core import REPO_ROOT
from shared.contracts.validation import validate_pair

sys.path.insert(0, str(REPO_ROOT / "detectors" / "owner-b" / "client-collectors" / "python"))
from owner_b_explain import CHECK_CONTEXTS, ExplainCollector, is_explainable, redact_plan  # noqa: E402

FIXTURES = REPO_ROOT / "detectors" / "owner-b" / "test" / "fixtures" / "explain"
ENTRY = REPO_ROOT / "detectors" / "owner-b" / "index.js"
SHA = "a" * 40


def fixture(name):
    return json.loads((FIXTURES / f"pg16-{name}.json").read_text(encoding="utf-8"))


class FakeConnection:
    """DB-API connection returning one prepared plan; records every statement it was asked to run."""

    def __init__(self, plan, log):
        self.plan, self.log, self.rolled_back, self.closed = plan, log, False, False

    def cursor(self):
        return self

    def execute(self, statement, params=None):
        self.log.append((statement, params))

    def fetchone(self):
        return (self.plan,)

    def rollback(self):
        self.rolled_back = True

    def close(self):
        self.closed = True


class ExplainCollectorTest(unittest.TestCase):
    def test_only_plain_reads_are_explainable(self):
        for ok in ("SELECT 1", " select * from t where a = %s", "WITH x AS (SELECT 1) SELECT * FROM x", "(SELECT 1)"):
            self.assertTrue(is_explainable(ok), ok)
        for bad in ("UPDATE t SET a = 1", "DELETE FROM t", "INSERT INTO t VALUES (1)", "SELECT * FROM t FOR UPDATE", "SELECT * INTO u FROM t",
                    "WITH d AS (DELETE FROM t RETURNING *) SELECT * FROM d", "DROP TABLE t", "EXPLAIN SELECT 1"):
            self.assertFalse(is_explainable(bad), bad)
        self.assertTrue(is_explainable("SELECT 'update me' FROM t"))

    def test_records_once_runs_in_a_rolled_back_transaction_and_skips_writes(self):
        collector, log, conns = ExplainCollector(), [], []

        def factory():
            conns.append(FakeConnection(fixture("seqscan-filter"), log))
            return conns[-1]

        self.assertTrue(collector.record(factory, "SELECT * FROM orders WHERE status = %s", ("refunded",), "app/a.py:1"))
        self.assertFalse(collector.record(factory, "SELECT *   FROM orders WHERE status = %s", ("paid",), "app/a.py:2"))
        self.assertFalse(collector.record(factory, "UPDATE orders SET status = 'x'", None, "app/a.py:3"))
        self.assertEqual(collector.skipped, {"not_read_only": 1, "duplicate": 1, "limit": 0, "failed": 0})
        self.assertEqual(len(conns), 1)
        self.assertTrue(conns[0].rolled_back and conns[0].closed)
        self.assertTrue(log[0][0].startswith("SET LOCAL statement_timeout"))
        self.assertTrue(log[1][0].startswith("EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) SELECT"))
        self.assertEqual(log[1][1], ("refunded",), "parameters reach EXPLAIN but are never stored")

    def test_literal_values_in_plan_conditions_are_redacted(self):
        plan = fixture("seqscan-filter")
        self.assertIn("refunded", plan[0]["Plan"]["Filter"])  # the real plan embeds the parameter value
        collector = ExplainCollector()
        collector.record(lambda: FakeConnection(plan, []), "SELECT * FROM orders WHERE status = %s", ("refunded",), "app/a.py:1")
        stored = json.dumps(collector.contract_inputs("github:t/t", SHA))
        self.assertNotIn("refunded", stored)
        redacted = redact_plan([{"Plan": {"Node Type": "Seq Scan", "Filter": "((email)::text = 'a@b.com'::text AND (t1.col2 > 42) AND (price <= 3.5))"}}])
        self.assertEqual(redacted[0]["Plan"]["Filter"], "((email)::text = '?'::text AND (t1.col2 > ?) AND (price <= ?))")

    def test_failures_never_raise_and_the_limit_is_enforced(self):
        collector = ExplainCollector(max_queries=1)

        def broken():
            raise RuntimeError("connection refused")

        self.assertFalse(collector.record(broken, "SELECT 1 FROM a", None, "x:1"))
        self.assertEqual(collector.skipped["failed"], 1)
        self.assertTrue(collector.record(lambda: FakeConnection(fixture("index-lookup"), []), "SELECT 1 FROM b", None, "x:2"))
        self.assertFalse(collector.record(lambda: FakeConnection(fixture("index-lookup"), []), "SELECT 1 FROM c", None, "x:3"))
        self.assertEqual(collector.skipped["limit"], 1)

    def test_commit_sha_must_be_a_full_lowercase_sha(self):
        collector = ExplainCollector()
        collector.record(lambda: FakeConnection(fixture("index-lookup"), []), "SELECT 1 FROM b", None, "x:1")
        with self.assertRaises(ValueError):
            collector.contract_inputs("github:t/t", "main")

    def test_collected_plans_flow_through_the_detectors_with_valid_evidence(self):
        collector = ExplainCollector()
        real = {"SELECT * FROM orders WHERE status = %s": "seqscan-filter", "SELECT * FROM orders ORDER BY total": "sort-spill-serial",
                "SELECT c.id FROM customers_noidx c JOIN customers_noidx d ON d.id = c.id WHERE c.id <= 300": "nestloop-inner-seqscan",
                "SELECT * FROM orders WHERE id = %s": "index-lookup"}
        for sql, name in real.items():
            collector.record(lambda name=name: FakeConnection(fixture(name), []), sql, None, "app/x.py:1")
        with tempfile.TemporaryDirectory() as out:
            paths = collector.write_contract_inputs(out, "github:t/t", SHA)
            self.assertEqual({p.name for p in paths}, {f"{check.lower()}-input.json" for check in CHECK_CONTEXTS})
            inputs = [json.loads(p.read_text(encoding="utf-8")) for p in paths]
        try:
            response = node.call("owner-b", ENTRY, "evaluate", {"inputs": inputs})
        except node.AdapterUnavailable as error:  # no Node on this machine: nothing to verify here
            self.skipTest(str(error))
        found = {}
        for payload, item in zip(inputs, response["results"]):
            self.assertIn("result", item, item)
            validate_pair(payload, item["result"])
            self.assertEqual(item["result"]["status"], "completed")
            found[payload["check_id"]] = sorted(f["identity"] for f in item["result"]["findings"])
        expected = {"DB-43": ["seq-scan:customers_noidx(c)", "seq-scan:orders"], "DB-45": ["sort-disk:total"],
                    "DB-46": ["nested-loop-inner-seq-scan:customers_noidx(d)"]}
        self.assertEqual(found, {check: expected[check] for check in CHECK_CONTEXTS})


if __name__ == "__main__":
    unittest.main()
