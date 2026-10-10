"""Scan API handlers with in-memory S3/Lambda fakes: no AWS calls and no network."""
import io
import json
import os
import shutil
import tarfile
import tempfile
import threading
import unittest
import uuid
from pathlib import Path
from unittest import mock

from scan_api import api, store, worker

SHA = "a" * 40
SERVICE = b"def add_item(item, bucket=[]):\n    bucket.append(item)\n    return bucket\n"


class NoSuchKey(Exception):
    response = {"Error": {"Code": "NoSuchKey"}}


class FakeS3:
    def __init__(self):
        self.objects, self.statuses = {}, []

    def put_object(self, Bucket, Key, Body, ContentType):
        self.objects[Key] = Body
        if Key.endswith("status.json"):
            self.statuses.append(json.loads(Body)["status"])

    def get_object(self, Bucket, Key):
        if Key not in self.objects:
            raise NoSuchKey(Key)
        return {"Body": io.BytesIO(self.objects[Key])}

    def json(self, scan_id, name):
        return json.loads(self.objects[store.key(scan_id, name)])


class ConditionalCheckFailed(Exception):
    response = {"Error": {"Code": "ConditionalCheckFailedException"}}


class FakeDynamo:
    """The abuse-guard table: get/put items and the two conditional `ADD scans` updates, atomic under a lock."""

    def __init__(self):
        self.items, self.lock, self.fail_with = {}, threading.Lock(), None

    def get_item(self, TableName, Key, ConsistentRead):
        item = self.items.get(Key["pk"]["S"])
        return {"Item": dict(item)} if item else {}

    def put_item(self, TableName, Item):
        self.items[Item["pk"]["S"]] = dict(Item)

    def update_item(self, TableName, Key, UpdateExpression, ConditionExpression, ExpressionAttributeValues):
        with self.lock:
            if self.fail_with:
                raise self.fail_with
            pk, values = Key["pk"]["S"], ExpressionAttributeValues
            item = self.items.get(pk, {"pk": Key["pk"]})
            count = int(item["scans"]["N"]) if "scans" in item else None
            if ":cap" in values:  # reserve: ADD scans :one, only while under the cap
                ok, delta = count is None or count < int(values[":cap"]["N"]), 1
            else:  # release: ADD scans :minus_one, never below zero
                ok, delta = count is not None and count > 0, -1
            if not ok:
                raise ConditionalCheckFailed(pk)
            item = {**item, "scans": {"N": str((count or 0) + delta)}}
            if ":expires" in values:
                item["expires_at"] = values[":expires"]
            self.items[pk] = item

    def count(self, day):
        return int(self.items.get(f"day#{day}", {}).get("scans", {"N": "0"})["N"])


def make_tarball(path: Path, entries, comment=SHA):
    """entries: [(name, bytes | None for a dir | ("symlink", target))]."""
    with tarfile.open(path, "w:gz", format=tarfile.PAX_FORMAT, pax_headers={"comment": comment}) as tar:
        for name, data in entries:
            info = tarfile.TarInfo(name)
            if data is None:
                info.type = tarfile.DIRTYPE
                tar.addfile(info)
            elif isinstance(data, tuple):
                info.type, info.linkname = tarfile.SYMTYPE, data[1]
                tar.addfile(info)
            else:
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))


class ScanApiTest(unittest.TestCase):
    def setUp(self):
        self.s3, self.lam, self.ddb = FakeS3(), mock.Mock(), FakeDynamo()
        clients = {"s3": self.s3, "lambda": self.lam, "dynamodb": self.ddb}
        patches = [mock.patch.object(store, "client", lambda name: clients[name]),
                   mock.patch.dict(os.environ, {"SCAN_BUCKET": "b", "WORKER_FUNCTION_NAME": "worker",
                                                "SCAN_GUARD_TABLE": "scan-api-guard"})]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def post(self, body):
        response = api.handler({"routeKey": "POST /scans", "body": json.dumps(body)})
        return response["statusCode"], json.loads(response["body"])

    def get(self, scan_id):
        response = api.handler({"routeKey": "GET /scans/{scan_id}", "pathParameters": {"scan_id": scan_id}})
        return response["statusCode"], json.loads(response["body"])

    def test_post_validates_repo_url(self):
        for bad in ("https://gitlab.com/owner/repo", "http://github.com/owner/repo", "https://github.com/owner",
                    "https://github.com/owner/repo/tree/main", "https://github.com/../repo", "git@github.com:o/r.git"):
            self.assertEqual(self.post({"repo_url": bad})[0], 400, bad)
        self.assertEqual(self.post({"url": "https://github.com/owner/repo"})[0], 400)
        self.lam.invoke.assert_not_called()
        self.assertEqual(self.s3.objects, {})

    def test_post_queues_scan_and_invokes_worker_async(self):
        status, body = self.post({"repo_url": " https://github.com/Owner/my.repo.git "})
        self.assertEqual(status, 202)
        scan_id = body["scan_id"]
        self.assertEqual(self.s3.json(scan_id, "status.json")["status"], "queued")
        call = self.lam.invoke.call_args.kwargs
        self.assertEqual((call["FunctionName"], call["InvocationType"]), ("worker", "Event"))
        self.assertEqual(json.loads(call["Payload"])["repo_url"], "https://github.com/Owner/my.repo")
        self.assertEqual(self.get(scan_id)[1]["status"], "queued")

    def test_get_reports_done_error_stale_and_unknown(self):
        done, failed, stale = (str(uuid.uuid4()) for _ in range(3))
        store.put_json(done, "report.json", {"report_version": "1.0"})
        store.put_status(done, "done", "https://github.com/o/r", store.now(), report_bytes=24)
        store.put_status(failed, "error", "https://github.com/o/r", store.now(), error="repository is empty")
        store.put_status(stale, "running", "https://github.com/o/r", store.now())
        self.s3.objects[store.key(stale, "status.json")] = json.dumps(
            {**self.s3.json(stale, "status.json"), "updated_at": "2020-01-01T00:00:00+00:00"}).encode()

        self.assertEqual(self.get(done)[1]["report"], {"report_version": "1.0"})
        self.assertEqual(self.get(failed)[1]["error"], "repository is empty")
        self.assertEqual(self.get(stale)[1]["status"], "error")  # worker died without recording it
        self.assertEqual(self.get(str(uuid.uuid4()))[0], 404)
        self.assertEqual(self.get("../../etc")[0], 404)

    def test_worker_scans_tarball_and_writes_report(self):
        fixture = self.tmp / "fixture.tar.gz"
        top = "owner-repo-aaaaaaa"
        make_tarball(fixture, [(top, None), (f"{top}/app", None), (f"{top}/app/service.py", SERVICE),
                               (f"{top}/escape", ("symlink", "/etc/passwd"))])
        scan_id = str(uuid.uuid4())
        with mock.patch.object(worker, "resolve_sha", return_value=SHA), \
                mock.patch.object(worker, "LAMBDA_OWNERS", {"C", "D"}), \
                mock.patch.object(worker, "download", side_effect=lambda url, path: shutil.copy(fixture, path)) as dl:
            worker.handler({"scan_id": scan_id, "repo_url": "https://github.com/owner/repo"})

        self.assertEqual(dl.call_args.args[0], f"https://codeload.github.com/owner/repo/tar.gz/{SHA}")
        self.assertEqual(self.s3.statuses, ["running", "done"])
        report = self.s3.json(scan_id, "report.json")
        self.assertEqual((report["scan_id"], report["repository"]["commit_sha"], report["repository"]["commit_source"]),
                         (scan_id, SHA, "github-api"))
        self.assertEqual(report["files"]["collected"], 1)  # the symlink was never extracted
        self.assertIn(("PY-09", "app/service.py", 1), [(f["check_id"], f["file"], f["line"]) for f in report["findings"]])
        unavailable = {a["owner"]: a["reason"] for a in report["adapters"] if a["status"] == "unavailable"}
        self.assertEqual(unavailable, {"A": worker.NODE_REASON, "B": worker.NODE_REASON})

    def test_worker_failure_records_error(self):
        scan_id = str(uuid.uuid4())
        with mock.patch.object(worker, "resolve_sha", side_effect=worker.ScanError("repository not found or not public")):
            worker.handler({"scan_id": scan_id, "repo_url": "https://github.com/owner/missing"})
        self.assertEqual(self.s3.statuses, ["running", "error"])
        self.assertEqual(self.get(scan_id)[1]["error"], "repository not found or not public")

    def test_tarball_path_traversal_is_rejected(self):
        for name in ("../evil.py", "/abs/evil.py", "top/../../evil.py"):
            tarball, dest = self.tmp / "bad.tar.gz", self.tmp / name.replace("/", "_")
            make_tarball(tarball, [("top/ok.py", b"x = 1\n"), (name, b"x = 1\n")])
            with self.assertRaisesRegex(worker.ScanError, "unsafe path"):
                worker.safe_extract(tarball, dest)
            self.assertFalse((self.tmp / "evil.py").exists())


if __name__ == "__main__":
    unittest.main()
