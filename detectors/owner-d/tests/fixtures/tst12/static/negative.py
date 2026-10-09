# Synthetic TST-12 fixture (TST-12-02): look-alikes that do no real wait/network/big allocation.
# Supplied as tests/test_service.py. Never imported or executed by the detector.
import asyncio
import os
import time

import boto3
import pytest
import requests

LOCAL = "http://localhost:8080"


def wait_until(predicate):
    # Helper, not a test: helpers in test modules are not followed.
    while not predicate():
        time.sleep(1)


def test_local_server_and_dynamic_urls(base_url):
    requests.get(LOCAL + "/health")
    requests.get("http://127.0.0.1:9000/ping")
    requests.get("https://service.test/api")
    requests.get(base_url)
    requests.get(os.environ["SERVICE_URL"])


def test_trivial_and_unknown_sleeps(delay):
    time.sleep(0)
    time.sleep(0.05)
    time.sleep(delay)


async def test_sleep_cut_short_by_timeout():
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(asyncio.sleep(10), timeout=0.01)
    async with asyncio.timeout(0.01):
        await asyncio.sleep(10)
    task = asyncio.create_task(asyncio.sleep(10))
    task.cancel()


def test_nested_callback_is_not_run_here():
    def slow_side_effect(*args):
        time.sleep(5)
        return requests.get("https://api.pypi.org/simple")

    assert callable(slow_side_effect)


def test_retry_only_on_error():
    try:
        os.remove("missing")
    except OSError:
        time.sleep(1)


def test_small_payload_and_local_aws():
    data = b"x" * 1024
    s3 = boto3.client("s3", endpoint_url="http://localhost:4566")
    s3.list_buckets()
    url = s3.generate_presigned_url("get_object", Params={"Bucket": "b", "Key": "k"})
    assert data and url


class Helper:
    def test_like_method_on_non_test_class(self):
        time.sleep(3)
