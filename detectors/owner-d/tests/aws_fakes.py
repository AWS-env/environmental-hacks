"""Stubbed boto3 clients for the telemetry Lambda tests. No network: every client is a fake that only
implements the read APIs the analyzers may call, so any write call fails with AttributeError."""

import contextlib
import datetime as dt
import io
import os
import sys
import unittest
from pathlib import Path

DETECTOR_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = Path(__file__).resolve().parents[3]
for path in (DETECTOR_DIR, REPO_ROOT, REPO_ROOT / "hub"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from owner_d.aws import common  # noqa: E402

SHA = "0123456789abcdef0123456789abcdef01234567"
ROLE = "arn:aws:iam::123456789012:role/owner-d-telemetry-readonly"  # AWS documentation placeholder
NOW = dt.datetime(2026, 10, 10, 12, 0, tzinfo=dt.timezone.utc)


class ClientError(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.response = {"Error": {"Code": code}}


def hourly(days, value, *, end=NOW, period=3600, start_offset_days=None):
    """Timestamps/values for `days` of data ending at `end` (aligned to the period)."""
    end = dt.datetime.fromtimestamp(int(end.timestamp()) // period * period, dt.timezone.utc)
    count = int(days * 86400 // period)
    first = end - dt.timedelta(seconds=count * period)
    stamps = [first + dt.timedelta(seconds=i * period) for i in range(count)]
    values = [value(i) if callable(value) else value for i in range(count)]
    return stamps, values


class FakeCloudWatch:
    """ListMetrics pages + GetMetricData from {dimension-key: (avg_series, max_series)}."""

    def __init__(self, data=None, list_pages=None, data_pages=1):
        self.data = data or {}
        self.list_pages = list_pages or [[]]
        self.data_pages = data_pages
        self.calls = []

    def list_metrics(self, **kw):
        self.calls.append(("list_metrics", kw))
        index = int(kw.get("NextToken", "0"))
        page = {"Metrics": [m for m in self.list_pages[index]
                            if kw.get("Namespace") in (None, m["Namespace"])]}
        if index + 1 < len(self.list_pages):
            page["NextToken"] = str(index + 1)
        return page

    def get_metric_data(self, **kw):
        self.calls.append(("get_metric_data", kw))
        page = int(kw.get("NextToken", "0"))
        results = []
        for query in kw["MetricDataQueries"]:
            stat = query["MetricStat"]
            key = tuple(d["Value"] for d in stat["Metric"]["Dimensions"])
            avg, peak = self.data.get(key, (([], []), ([], [])))
            stamps, values = avg if stat["Stat"] == "Average" else peak
            # split each series across data_pages pages, like CloudWatch's NextToken paging
            size = -(-len(stamps) // self.data_pages) if stamps else 0
            chunk = slice(page * size, (page + 1) * size)
            results.append({"Id": query["Id"], "Timestamps": stamps[chunk], "Values": values[chunk],
                            "StatusCode": "PartialData" if page + 1 < self.data_pages else "Complete"})
        out = {"MetricDataResults": results}
        if page + 1 < self.data_pages:
            out["NextToken"] = str(page + 1)
        return out


class FakeEvents:
    def __init__(self, bus_exists=True):
        self.entries, self.bus_exists = [], bus_exists

    def describe_event_bus(self, Name):
        if not self.bus_exists:
            raise ClientError("ResourceNotFoundException")
        return {"Name": Name}

    def put_events(self, Entries):
        assert len(Entries) <= 10
        self.entries.extend(Entries)
        return {"FailedEntryCount": 0, "Entries": [{"EventId": str(i)} for i in range(len(Entries))]}


class FakeSts:
    def __init__(self, expires_in=dt.timedelta(minutes=15)):
        self.calls, self.expires_in = [], expires_in

    def assume_role(self, **kw):
        self.calls.append(kw)
        return {"Credentials": {"AccessKeyId": f"ASIA{len(self.calls)}", "SecretAccessKey": "secret",
                                "SessionToken": "token", "Expiration": common._now() + self.expires_in}}


class FakeLogs:
    def __init__(self, groups=(), page_size=2, statuses=("Running", "Complete"), rows=(), tags=None):
        self.groups, self.page_size = list(groups), page_size
        self.statuses, self.rows, self.tags = list(statuses), list(rows), tags or {}
        self.calls = []

    def describe_log_groups(self, **kw):
        self.calls.append(("describe_log_groups", kw))
        prefix = kw.get("logGroupNamePrefix", "")
        matching = [g for g in self.groups if g["logGroupName"].startswith(prefix)]
        start = int(kw.get("nextToken", "0"))
        page = {"logGroups": matching[start:start + self.page_size]}
        if start + self.page_size < len(matching):
            page["nextToken"] = str(start + self.page_size)
        return page

    def list_tags_for_resource(self, resourceArn):
        self.calls.append(("list_tags_for_resource", resourceArn))
        if resourceArn not in self.tags:
            raise ClientError("AccessDeniedException")
        return {"tags": self.tags[resourceArn]}

    def start_query(self, **kw):
        self.calls.append(("start_query", kw))
        return {"queryId": "q-1"}

    def get_query_results(self, queryId):
        self.calls.append(("get_query_results", queryId))
        status = self.statuses.pop(0) if len(self.statuses) > 1 else self.statuses[0]
        out = {"status": status, "statistics": {"recordsMatched": 2.0, "recordsScanned": 10.0, "bytesScanned": 2048.0}}
        if status == "Complete":
            out["results"] = [[{"field": k, "value": v} for k, v in row.items()] + [{"field": "@ptr", "value": "p"}]
                              for row in self.rows]
        return out

    def stop_query(self, queryId):
        self.calls.append(("stop_query", queryId))
        return {"success": True}


class FakeXray:
    def __init__(self, trace_ids=(), page_size=5):
        self.trace_ids, self.page_size, self.calls = list(trace_ids), page_size, []

    def get_trace_summaries(self, **kw):
        self.calls.append(("get_trace_summaries", kw))
        start = int(kw.get("NextToken", "0"))
        page = {"TraceSummaries": [{"Id": i, "Duration": 1.0} for i in self.trace_ids[start:start + self.page_size]]}
        if start + self.page_size < len(self.trace_ids):
            page["NextToken"] = str(start + self.page_size)
        return page

    def batch_get_traces(self, TraceIds, **kw):
        assert len(TraceIds) <= 5, "BatchGetTraces accepts at most 5 ids"
        self.calls.append(("batch_get_traces", tuple(TraceIds)))
        return {"Traces": [{"Id": i, "Duration": 1.0, "Segments": [{"Id": "s", "Document": "{}"}]} for i in TraceIds],
                "UnprocessedTraceIds": []}


class Context:
    def __init__(self, ms=120_000):
        self.ms = ms

    def get_remaining_time_in_millis(self):
        return self.ms


class AwsTestCase(unittest.TestCase):
    """Installs a client factory that hands out fakes and records which credentials each client used."""

    def setUp(self):
        self._env = mock_env({"AWS_REGION": "ap-south-1", "FINDINGS_BUS_NAME": "findings-hub",
                              "LOG_GROUP_ALLOWLIST": "/aws/lambda/owner-d-*"})
        self.fakes = {"cloudwatch": FakeCloudWatch(), "events": FakeEvents(), "sts": FakeSts(),
                      "logs": FakeLogs(), "xray": FakeXray()}
        self.created = []

        def factory(service, region, credentials):
            self.created.append((service, region, credentials and credentials["AccessKeyId"]))
            return self.fakes[service]

        self._quiet = contextlib.redirect_stdout(io.StringIO())  # handlers print one JSON summary line
        self._quiet.__enter__()
        self._saved = (common._factory, common._now, common._sleep, common._monotonic)
        common._factory = factory
        common._now = lambda: NOW
        common._sleep = lambda seconds: None
        common._clients.clear()
        common._sessions.clear()
        common._verified_buses.clear()

    def tearDown(self):
        common._factory, common._now, common._sleep, common._monotonic = self._saved
        common._clients.clear()
        common._sessions.clear()
        common._verified_buses.clear()
        self._env()
        self._quiet.__exit__(None, None, None)

    def base_event(self, **extra):
        return {"repository_id": "github:AWS-env/example", "commit_sha": SHA, "scan_id": "scan-1", **extra}


def mock_env(values):
    saved = {k: os.environ.get(k) for k in values}
    os.environ.update(values)

    def restore():
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    return restore
