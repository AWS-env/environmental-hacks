# Synthetic TST-12 fixture (TST-12-01): unit tests doing real waits, network and big fixtures.
# Supplied as tests/test_client.py. Never imported or executed by the detector.
import asyncio
import time

import boto3
import pytest
import requests

API = "https://api.pypi.org/pypi"
RETRY_DELAY = 1.5


@pytest.fixture(scope="session")
def large_payload():
    return b"\0" * 64 * 1024 * 1024


def test_fetch_package(large_payload):
    response = requests.get(f"{API}/requests/json", timeout=5)
    assert response.status_code == 200


def test_retry_waits_between_attempts():
    time.sleep(RETRY_DELAY)
    for _ in range(3):
        time.sleep(0.2)
    assert True


class TestUploader:
    def setup_method(self):
        self.s3 = boto3.client("s3")

    def test_upload(self):
        self.s3.put_object(Bucket="bucket", Key="key", Body=b"x")
        client = boto3.client("s3")
        client.list_buckets()

    async def test_async_backoff(self):
        await asyncio.sleep(0.5)
