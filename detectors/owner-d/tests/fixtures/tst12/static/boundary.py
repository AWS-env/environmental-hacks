# Synthetic TST-12 fixture (TST-12-06): values exactly at, and just over, each limit.
# Supplied as tests/test_boundary.py with max_sleep_seconds 0.5, max_network_calls 1 and
# max_fixture_bytes 1048576. Never imported or executed by the detector.
import time

import requests


def test_sleep_at_limit():
    time.sleep(0.25)
    time.sleep(0.25)


def test_branches_take_one_path(flag):
    if flag:
        time.sleep(0.5)
    else:
        time.sleep(0.4)


def test_sleep_over_limit():
    time.sleep(0.25)
    time.sleep(0.3)


def test_one_call_at_limit():
    requests.get("https://api.pypi.org/simple")


def test_two_calls_over_limit():
    requests.get("https://api.pypi.org/simple")
    requests.head("https://files.pythonhosted.org/")


def test_payload_at_limit():
    assert bytearray(1048576)


def test_payload_over_limit():
    assert bytearray(1048577)
