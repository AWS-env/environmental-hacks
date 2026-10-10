import copy
import json
import unittest
import uuid
from decimal import Decimal

from findings_hub import api, writer
from tests.test_writer import FakeTable, load

SCAN = str(uuid.uuid4())


def stored_result(table, *, repository_id="github:AWS-env/example", scan_id=SCAN, source="owner-d.scan-api",
                  example="obs01-detected.json"):
    """Persist a contract example through the real writer layout, retargeted at repository/scan."""
    result = copy.deepcopy(load(example))
    result["repository_id"], result["scan_id"] = repository_id, scan_id
    item, findings = writer.build_items(result, evidence="unverified", source=source, event_id="evt",
                                        received_at="2026-10-10T12:00:00.000000Z")
    # DynamoDB hands numbers back as Decimal
    item = {k: Decimal(v) if isinstance(v, int) and not isinstance(v, bool) else v for k, v in item.items()}
    for row in [*findings, item]:
        table.items[(row["pk"], row["sk"])] = row
    return result


def get(owner="AWS-env", repo="example", scan_id=SCAN):
    response = api.handler({"routeKey": "GET /repos/{owner}/{repo}/scans/{scan_id}",
                            "pathParameters": {"owner": owner, "repo": repo, "scan_id": scan_id}})
    return response["statusCode"], json.loads(response["body"]), response


class HubApiTest(unittest.TestCase):
    def setUp(self):
        self.table = FakeTable()
        api._table = self.table
        self.addCleanup(setattr, api, "_table", None)

    def test_public_scan_returns_results_and_findings(self):
        result = stored_result(self.table)
        status, body, response = get()
        self.assertEqual(status, 200)
        self.assertEqual(response["headers"]["cache-control"], "no-store")
        self.assertEqual((body["repository_id"], body["scan_id"], body["commit_sha"]),
                         ("github:AWS-env/example", SCAN, result["commit_sha"]))
        self.assertEqual([r["check_id"] for r in body["results"]], [result["check_id"]])
        self.assertEqual(body["results"][0]["source"], "owner-d.scan-api")
        self.assertIsInstance(body["results"][0]["finding_count"], int)
        self.assertEqual(len(body["findings"]), len(result["findings"]))
        self.assertEqual(body["findings"][0]["evidence"], result["findings"][0]["evidence"])
        self.assertEqual((body["findings_total"], body["truncated"]), (len(result["findings"]), False))

    def test_other_sources_in_the_same_partition_are_hidden(self):
        stored_result(self.table)
        stored_result(self.table, source="owner-d.artifact-parser", example="obs01-clean.json")
        _, body, _ = get()
        self.assertEqual({r["source"] for r in body["results"]}, {"owner-d.scan-api"})

    def test_private_sources_alone_are_not_found(self):
        for source in ("owner-d.artifact-parser", "owner-d.telemetry-analyzer", "owner-b.detectors", "scan-api"):
            with self.subTest(source=source):
                self.table.items.clear()
                stored_result(self.table, source=source)
                self.assertEqual(get()[0], 404)

    def test_unknown_scan_and_other_repository(self):
        stored_result(self.table)
        self.assertEqual(get(scan_id=str(uuid.uuid4()))[0], 404)
        self.assertEqual(get(repo="other")[0], 404)

    def test_path_validation(self):
        for params in [{"scan_id": "gha-38021695271-1"}, {"scan_id": "../x"}, {"owner": "../.."},
                       {"repo": ".hidden"}, {"scan_id": SCAN.upper()}, {"owner": ""}]:
            with self.subTest(params=params):
                status, body, _ = get(**params)
                self.assertEqual((status, body), (404, {"error": "scan not found"}))

    def test_findings_are_capped(self):
        stored_result(self.table)
        finding = next(v for k, v in self.table.items.items() if k[1].startswith("FINDING#"))
        for i in range(api.MAX_FINDINGS + 5):
            row = dict(finding, sk=f"{finding['sk']}-{i}", fingerprint=f"{i:064x}")
            self.table.items[(row["pk"], row["sk"])] = row
        _, body, _ = get()
        self.assertEqual(len(body["findings"]), api.MAX_FINDINGS)
        self.assertTrue(body["truncated"])

    def test_table_errors_are_500_without_details(self):
        class Broken:
            def query(self, **kw):
                raise RuntimeError("AccessDeniedException: arn:aws:dynamodb:...")
        api._table = Broken()
        status, body, _ = get()
        self.assertEqual((status, body), (500, {"error": "internal error"}))


if __name__ == "__main__":
    unittest.main()
