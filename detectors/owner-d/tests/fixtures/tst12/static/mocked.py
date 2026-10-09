# Synthetic TST-12 fixture (TST-12-02): the positive patterns, but mocked in this module.
# Supplied as tests/test_mocked.py. Never imported or executed by the detector.
import time
from unittest import mock

import requests
import responses


@responses.activate
def test_fetch_package_is_mocked():
    responses.get("https://api.pypi.org/pypi/requests/json", json={})
    assert requests.get("https://api.pypi.org/pypi/requests/json").ok


@mock.patch("time.sleep")
def test_retry_with_patched_sleep(fake_sleep):
    time.sleep(30)
    fake_sleep.assert_called_once_with(30)
