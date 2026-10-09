import copy
import importlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from findings_hub import readback, writer
from findings_hub.store import canonical, sha256
from shared.contracts.validation import ContractError
from tests.test_writer import FakeTable, NOW, load

importer = importlib.import_module("findings_hub.import")


class ImportTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "pair.json"
        pair = {"input": load("obs01-input.json"), "result": load("obs01-detected.json")}
        self.envelope = {"pair": pair, "sha256": sha256(canonical(pair)),
                         "identity": {f: pair["result"][f] for f in writer.POINTER_FIELDS}}
        self.table = FakeTable()

    def save(self):
        self.path.write_text(json.dumps(self.envelope), encoding="utf-8")

    def test_valid_pair_is_verified_and_read_back(self):
        self.save()
        summary = importer.import_pair(self.path, self.table, now=NOW)
        self.assertEqual((summary["outcome"], summary["evidence"], summary["source"]),
                         ("stored", "verified", "owner-b.detectors"))
        result = self.envelope["pair"]["result"]
        report = readback.report(self.table, result["repository_id"], result["scan_id"])
        self.assertTrue(report["persisted"])
        self.assertEqual(report["results"][0]["artifact"]["hash_kind"], "canonical_local_pair_sha256")
        self.assertEqual(report["findings"][0]["fingerprint"], result["findings"][0]["fingerprint"])

    def test_sha_mismatch_rejected_before_write(self):
        self.envelope["sha256"] = "0" * 64
        self.save()
        with self.assertRaisesRegex(ContractError, "checksum"):
            importer.import_pair(self.path, self.table)
        self.assertEqual(self.table.items, {})

    def test_identity_mismatch_rejected_before_write(self):
        self.envelope["identity"]["scan_id"] = "wrong-scan"
        self.save()
        with self.assertRaisesRegex(ContractError, "identity"):
            importer.import_pair(self.path, self.table)
        self.assertEqual(self.table.items, {})

    def test_duplicate_leaves_stored_items_unchanged(self):
        self.save()
        importer.import_pair(self.path, self.table, now=NOW)
        before = copy.deepcopy(self.table.items)
        self.assertEqual(importer.import_pair(self.path, self.table)["outcome"], "duplicate")
        self.assertEqual(self.table.items, before)

    def test_invalid_contract_rejected_even_with_matching_checksum(self):
        self.envelope["pair"]["result"]["findings"][0]["fingerprint"] = "0" * 64
        self.envelope["sha256"] = sha256(canonical(self.envelope["pair"]))
        self.save()
        with self.assertRaises(ContractError):
            importer.import_pair(self.path, self.table)
        self.assertEqual(self.table.items, {})

    def test_verify_only_does_not_create_aws_client(self):
        self.save()
        with patch.dict("sys.modules", {"boto3": None}):
            self.assertEqual(importer.main(["--pair", str(self.path), "--verify-only"]), 0)
        self.assertEqual(self.table.items, {})

    def test_invalid_cli_input_does_not_create_aws_client(self):
        self.envelope["sha256"] = "0" * 64
        self.save()
        with patch.dict("sys.modules", {"boto3": None}), self.assertRaises(ContractError):
            importer.main(["--pair", str(self.path)])

    def test_oversize_input_is_rejected(self):
        self.path.write_bytes(b" " * (writer.MAX_ARTIFACT_BYTES + 1))
        with self.assertRaisesRegex(ContractError, "too large"):
            importer.import_pair(self.path, self.table)
        self.assertEqual(self.table.items, {})

    def test_cross_region_cli_rejected_before_client_creation(self):
        self.save()
        with patch.dict("sys.modules", {"boto3": None}), self.assertRaises(SystemExit) as caught:
            importer.main(["--pair", str(self.path), "--region", "eu-north-1"])
        self.assertEqual(caught.exception.code, 2)
