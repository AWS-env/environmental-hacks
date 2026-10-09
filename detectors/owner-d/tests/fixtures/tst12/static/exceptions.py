# Synthetic TST-12 fixture (TST-12-03): integration/slow/network tests and opted-out lines.
# Supplied as tests/test_exceptions.py. Never imported or executed by the detector.
import os
import time
import unittest

import pytest
import requests


@pytest.mark.integration
def test_pypi_round_trip():
    requests.get("https://api.pypi.org/simple")
    time.sleep(2)


@pytest.mark.skipif(not os.environ.get("NETWORK_TESTS"), reason="needs network access")
def test_network_opt_in():
    requests.get("https://api.pypi.org/simple")


def test_skips_without_internet():
    if not os.environ.get("CI_ONLINE"):
        pytest.skip("requires internet")
    requests.get("https://api.pypi.org/simple")


class TestLiveService(unittest.TestCase):
    def test_round_trip(self):
        time.sleep(1)


@pytest.mark.slow
class TestSlowSuite:
    def test_waits(self):
        time.sleep(1)


def test_documented_wait():
    time.sleep(1)  # noqa: TST-12 - waits for the OS clock tick under test
