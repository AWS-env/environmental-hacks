import copy
import io
import json
import unittest
from datetime import datetime, timezone
from pathlib import Path

from findings_hub import readback, writer
from findings_hub.store import canonical, scan_pk, sha256
from shared.contracts.validation import ContractError

EXAMPLES = Path(__file__).resolve().parents[2] / "shared/contracts/examples"
NOW = datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc)
BUCKET = "owner-b-artifacts"


def load(name):
    return json.loads((EXAMPLES / name).read_text())


class ClientError(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.response = {"Error": {"Code": code}}


class FakeTable:
    def __init__(self):
        self.items = {}

    def batch_writer(self):
        table = self

        class Batch:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def put_item(self, Item):
                table.items[(Item["pk"], Item["sk"])] = copy.deepcopy(Item)

        return Batch()

    def put_item(self, Item, ConditionExpression=None):
        key = (Item["pk"], Item["sk"])
        if ConditionExpression and key in self.items:
            raise ClientError("ConditionalCheckFailedException")
        self.items[key] = copy.deepcopy(Item)

    def query(self, KeyConditionExpression, ExpressionAttributeValues, **kwargs):
        value = ExpressionAttributeValues[":pk"]
        field = "gsi1pk" if kwargs.get("IndexName") else "pk"
        rows = [i for i in self.items.values() if i.get(field) == value]
        if field == "gsi1pk":
            rows.sort(key=lambda i: i["gsi1sk"], reverse=not kwargs.get("ScanIndexForward", True))
        return {"Items": copy.deepcopy(rows)}


class FakeS3:
    def __init__(self, objects=None):
        self.objects = objects or {}
        self.calls = []

    def get_object(self, **params):
        self.calls.append(params)
        body = self.objects[(params["Bucket"], params["Key"])]
        return {"Body": io.BytesIO(body), "ContentLength": len(body)}


def inline_event(result):
    return {"id": "evt-1", "source": "owner-c.python-detectors", "detail-type": writer.RESULT_TYPE, "detail": result}


def pointer_event(pair, bucket=BUCKET, mutate_text=None):
    text = canonical(pair)
    digest = sha256(text)
    key = f"results/jobs/{'0' * 32}/{digest}.json"
    body = (mutate_text(text) if mutate_text else text).encode("utf-8")
    result = pair["result"]
    detail = {f: result[f] for f in writer.POINTER_FIELDS}
    detail["artifact"] = {"bucket": bucket, "key": key, "sha256": digest}
    event = {"id": "evt-2", "source": "owner-b.detectors", "detail-type": writer.POINTER_TYPE, "detail": detail}
    return event, FakeS3({(bucket, key): body})


class WriterTest(unittest.TestCase):
    def setUp(self):
        self.table = FakeTable()

    def ingest(self, event, s3=None):
        return writer.ingest(event, s3=s3 or FakeS3(), table=self.table, allowed_buckets={BUCKET}, now=NOW)

    def test_inline_result_is_persisted_and_read_back(self):
        result = load("obs01-detected.json")
        summary = self.ingest(inline_event(result))
        self.assertEqual((summary["outcome"], summary["evidence"], summary["findings"]), ("stored", "unverified", 1))

        out = readback.report(self.table, result["repository_id"], result["scan_id"])
        self.assertTrue(out["persisted"])
        self.assertEqual(out["results"][0]["status"], "completed")
        self.assertEqual(out["results"][0]["source"], "owner-c.python-detectors")
        self.assertEqual(out["findings"][0]["fingerprint"], result["findings"][0]["fingerprint"])
        self.assertEqual(out["findings"][0]["evidence"], result["findings"][0]["evidence"])
        stored = self.table.items[(summary["pk"], summary["sk"])]
        self.assertEqual(json.loads(stored["result_json"]), result)

    def test_redelivery_is_idempotent(self):
        event = inline_event(load("obs01-detected.json"))
        self.ingest(event)
        before = dict(self.table.items)
        self.assertEqual(self.ingest(event)["outcome"], "duplicate")
        self.assertEqual(self.table.items.keys(), before.keys())

    def test_unavailable_result_is_persisted_without_findings(self):
        result = load("obs01-unavailable.json")
        summary = self.ingest(inline_event(result))
        self.assertEqual((summary["status"], summary["findings"]), ("unavailable", 0))
        self.assertTrue(readback.report(self.table, result["repository_id"], result["scan_id"])["persisted"])

    def test_invalid_inline_result_is_rejected_and_not_stored(self):
        result = load("obs01-detected.json")
        result["findings"][0]["fingerprint"] = "0" * 64
        with self.assertRaises(ContractError):
            self.ingest(inline_event(result))
        self.assertEqual(self.table.items, {})

    def test_inline_input_payload_is_rejected(self):
        with self.assertRaises(ContractError):
            self.ingest(inline_event(load("obs01-input.json")))

    def test_unsupported_detail_type_is_rejected(self):
        event = inline_event(load("obs01-detected.json"))
        event["detail-type"] = "something.else"
        with self.assertRaises(ContractError):
            self.ingest(event)

    def test_pointer_pair_is_evidence_verified(self):
        pair = {"input": load("obs01-input.json"), "result": load("obs01-detected.json")}
        event, s3 = pointer_event(pair)
        summary = self.ingest(event, s3)
        self.assertEqual((summary["outcome"], summary["evidence"]), ("stored", "verified"))
        stored = self.table.items[(summary["pk"], summary["sk"])]
        self.assertEqual(stored["artifact"]["bucket"], BUCKET)
        self.assertEqual(stored["source"], "owner-b.detectors")

    def test_pointer_with_invented_evidence_is_rejected(self):
        result = load("obs01-detected.json")
        result["findings"][0]["evidence"][0]["value"] = "log_level: TRACE"
        event, s3 = pointer_event({"input": load("obs01-input.json"), "result": result})
        with self.assertRaises(ContractError):
            self.ingest(event, s3)
        self.assertEqual(self.table.items, {})

    def test_pointer_checksum_mismatch_is_rejected(self):
        pair = {"input": load("obs01-input.json"), "result": load("obs01-detected.json")}
        event, s3 = pointer_event(pair, mutate_text=lambda t: t.replace("DEBUG", "DEBUg", 1))
        with self.assertRaises(ContractError):
            self.ingest(event, s3)

    def test_pointer_bucket_must_be_allowlisted(self):
        pair = {"input": load("obs01-input.json"), "result": load("obs01-detected.json")}
        event, s3 = pointer_event(pair, bucket="someone-elses-bucket")
        with self.assertRaises(ContractError):
            self.ingest(event, s3)
        self.assertEqual(s3.calls, [])

    def test_pointer_identity_must_match_result(self):
        pair = {"input": load("obs01-input.json"), "result": load("obs01-detected.json")}
        event, s3 = pointer_event(pair)
        event["detail"]["scan_id"] = "another-scan"
        with self.assertRaises(ContractError):
            self.ingest(event, s3)

    def test_pointer_key_must_be_content_addressed(self):
        pair = {"input": load("obs01-input.json"), "result": load("obs01-detected.json")}
        event, s3 = pointer_event(pair)
        event["detail"]["artifact"]["key"] = "inputs/anything.json"
        with self.assertRaises(ContractError):
            self.ingest(event, s3)
        self.assertEqual(s3.calls, [])


class ReadbackTest(unittest.TestCase):
    def test_findings_without_a_committed_result_are_not_reported(self):
        table = FakeTable()
        result = load("obs01-detected.json")
        item, findings = writer.build_items(result, evidence="unverified", source="s", event_id="e", received_at="t")
        for finding in findings:  # simulate a crash after findings, before the result item
            table.items[(finding["pk"], finding["sk"])] = finding
        out = readback.report(table, result["repository_id"], result["scan_id"])
        self.assertEqual((out["persisted"], out["findings"]), (False, []))

    def test_missing_scan_is_not_persisted(self):
        self.assertFalse(readback.report(FakeTable(), "github:x/y", "nope")["persisted"])

    def test_recent_scans_lists_newest_first(self):
        table = FakeTable()
        before, after = load("obs01-detected.json"), load("obs01-detected.json")
        after["scan_id"] = "scan-after"
        writer.ingest(inline_event(before), s3=None, table=table, allowed_buckets=set(), now=NOW)
        writer.ingest(inline_event(after), s3=None, table=table, allowed_buckets=set(),
                      now=NOW.replace(hour=13))
        scans = readback.recent_scans(table, before["repository_id"])
        self.assertEqual([s["scan_id"] for s in scans], ["scan-after", "scan-before"])

    def test_scan_partition_key_is_unambiguous(self):
        self.assertNotEqual(scan_pk("a#b", "c"), scan_pk("a", "b#c"))


if __name__ == "__main__":
    unittest.main()
