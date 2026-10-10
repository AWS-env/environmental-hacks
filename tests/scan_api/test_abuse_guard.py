"""POST /scans abuse guard (#469): per-repo cooldown and the atomic daily cap. In-memory fakes, no AWS."""
import json
import os
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from unittest import mock

from scan_api import api, guard, store
from test_scan_api import ConditionalCheckFailed, FakeDynamo, FakeS3

DAY = "2026-10-10"
T0 = datetime.fromisoformat(f"{DAY}T12:00:00+00:00")


class AbuseGuardTest(unittest.TestCase):
    def setUp(self):
        self.s3, self.lam, self.ddb = FakeS3(), mock.Mock(), FakeDynamo()
        self.clock = T0
        clients = {"s3": self.s3, "lambda": self.lam, "dynamodb": self.ddb}
        patches = [mock.patch.object(store, "client", lambda name: clients[name]),
                   mock.patch.object(store, "now", lambda: self.clock.isoformat(timespec="seconds")),
                   mock.patch.object(api, "SCAN_COOLDOWN_SECONDS", 600),
                   mock.patch.object(api, "MAX_SCANS_PER_DAY", 50),
                   mock.patch.dict(os.environ, {"SCAN_BUCKET": "b", "WORKER_FUNCTION_NAME": "worker",
                                                "SCAN_GUARD_TABLE": "scan-api-guard"})]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    def post(self, repo="owner/repo"):
        response = api.handler({"routeKey": "POST /scans",
                                "body": json.dumps({"repo_url": f"https://github.com/{repo}"})})
        return response["statusCode"], json.loads(response["body"])

    def advance(self, seconds):
        self.clock += timedelta(seconds=seconds)

    def set_status(self, scan_id, state, **extra):
        doc = self.s3.json(scan_id, "status.json")
        store.put_status(scan_id, state, doc["repo_url"], doc["created_at"], **extra)

    def fill_cap(self):
        for i in range(50):
            self.assertEqual(self.post(f"owner/repo-{i}")[0], 202, i)

    # ---- cooldown ---------------------------------------------------------------------------
    def test_cooldown_reuses_queued_and_running_scan(self):
        status, first = self.post()
        self.assertEqual((status, first), (202, {"scan_id": first["scan_id"], "status": "queued"}))
        self.advance(30)
        self.assertEqual(self.post(), (200, {"scan_id": first["scan_id"], "status": "queued", "reused": True}))
        self.set_status(first["scan_id"], "running", hub=None)  # extra status.json fields are tolerated
        self.advance(300)
        self.assertEqual(self.post(), (200, {"scan_id": first["scan_id"], "status": "running", "reused": True}))
        self.assertEqual(self.lam.invoke.call_count, 1)

    def test_cooldown_matches_repo_case_insensitively(self):
        _, first = self.post("Owner/My.Repo")
        status, body = self.post("owner/my.repo.git")
        self.assertEqual((status, body["scan_id"], body["reused"]), (200, first["scan_id"], True))
        self.assertEqual(self.lam.invoke.call_count, 1)
        self.assertEqual(self.post("owner/other-repo")[0], 202)

    def test_done_scan_is_reused_within_cooldown_and_not_after(self):
        _, first = self.post()
        self.advance(100)
        self.set_status(first["scan_id"], "done", report_bytes=2, hub={"bus": "findings-hub", "published": 1})
        self.advance(600)  # exactly ScanCooldownSeconds after it finished
        self.assertEqual(self.post(), (200, {"scan_id": first["scan_id"], "status": "done", "reused": True}))
        self.advance(1)
        status, second = self.post()
        self.assertEqual(status, 202)
        self.assertNotEqual(second["scan_id"], first["scan_id"])
        self.assertEqual(self.ddb.count(DAY), 2)

    def test_errored_scan_is_ignored(self):
        _, first = self.post()
        self.set_status(first["scan_id"], "error", error="repository not found or not public")
        status, second = self.post()
        self.assertEqual(status, 202)
        self.assertNotEqual(second["scan_id"], first["scan_id"])

    def test_stale_scans_are_ignored(self):
        _, queued = self.post("owner/queued")
        _, running = self.post("owner/running")
        self.set_status(running["scan_id"], "running")
        self.advance(api.QUEUED_STALE_SECONDS + 1)  # also past RUNNING_STALE_SECONDS since the last update
        for repo, old in (("owner/queued", queued), ("owner/running", running)):
            status, body = self.post(repo)
            self.assertEqual(status, 202, repo)
            self.assertNotEqual(body["scan_id"], old["scan_id"])

    def test_cooldown_hits_do_not_consume_the_cap(self):
        self.post()
        for _ in range(10):
            self.assertEqual(self.post()[0], 200)
        self.assertEqual(self.ddb.count(DAY), 1)
        with mock.patch.object(api, "MAX_SCANS_PER_DAY", 1):
            self.assertEqual(self.post()[0], 200)  # still served at the cap
            self.assertEqual(self.post("owner/new")[0], 429)

    # ---- daily cap --------------------------------------------------------------------------
    def test_daily_cap_boundary(self):
        self.fill_cap()  # the 50th succeeds
        self.assertEqual(self.ddb.count(DAY), 50)
        self.assertEqual(self.post("owner/repo-50"),
                         (429, {"error": "daily scan limit reached; try again after 00:00 UTC"}))
        self.assertEqual(self.lam.invoke.call_count, 50)
        self.assertEqual(self.ddb.count(DAY), 50)  # a refused request counts nothing
        self.assertNotIn("repo#owner/repo-50", self.ddb.items)
        self.assertEqual(len([k for k in self.s3.objects if k.endswith("status.json")]), 50)

    def test_day_rollover_resets_the_cap(self):
        self.fill_cap()
        self.clock = datetime.fromisoformat(f"{DAY}T23:59:59+00:00")
        self.assertEqual(self.post("owner/late")[0], 429)
        self.advance(1)  # 00:00:00 UTC
        self.assertEqual(self.post("owner/late")[0], 202)
        self.assertEqual((self.ddb.count(DAY), self.ddb.count("2026-10-11")), (50, 1))
        expires = int(self.ddb.items["day#2026-10-11"]["expires_at"]["N"])
        self.assertEqual(expires, int(datetime.fromisoformat("2026-10-13T00:00:00+00:00").timestamp()))

    def test_conditional_check_failure_maps_to_429(self):
        self.ddb.fail_with = ConditionalCheckFailed("day")
        self.assertEqual(self.post()[0], 429)
        self.lam.invoke.assert_not_called()
        self.assertEqual(self.s3.objects, {})

    def test_other_counter_errors_are_500(self):
        error = type("Throttled", (Exception,), {"response": {"Error": {"Code": "ThrottlingException"}}})
        self.ddb.fail_with = error("slow down")
        self.assertEqual(self.post(), (500, {"error": "internal error"}))
        self.lam.invoke.assert_not_called()

    def test_concurrent_increments_never_exceed_the_cap(self):
        with ThreadPoolExecutor(max_workers=16) as pool:
            results = list(pool.map(lambda i: guard.reserve_daily_slot(DAY, 50), range(80)))
        self.assertEqual((results.count(True), results.count(False)), (50, 30))
        self.assertEqual(self.ddb.count(DAY), 50)

    def test_concurrent_posts_never_exceed_the_cap(self):
        with ThreadPoolExecutor(max_workers=16) as pool:
            statuses = [s for s, _ in pool.map(lambda i: self.post(f"owner/repo-{i}"), range(70))]
        self.assertEqual((statuses.count(202), statuses.count(429)), (50, 20))
        self.assertEqual(self.lam.invoke.call_count, 50)

    def test_failed_invoke_releases_the_slot_and_is_not_reused(self):
        self.lam.invoke.side_effect = RuntimeError("throttled")
        status, _ = self.post()
        self.assertEqual(status, 503)
        self.assertEqual(self.ddb.count(DAY), 0)
        self.assertEqual(self.s3.statuses, ["queued", "error"])
        self.lam.invoke.side_effect = None
        self.assertEqual(self.post()[0], 202)  # the errored scan does not trigger the cooldown
        self.assertEqual(self.ddb.count(DAY), 1)


if __name__ == "__main__":
    unittest.main()
