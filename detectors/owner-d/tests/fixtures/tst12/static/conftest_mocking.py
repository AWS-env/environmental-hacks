# Synthetic TST-12 fixture (TST-12-08): an ancestor conftest.py that mocks HTTP for every test.
# Supplied as tests/conftest.py. Never imported or executed by the detector.
import pytest
import responses


@pytest.fixture(autouse=True)
def mocked_http():
    with responses.RequestsMock(assert_all_requests_are_fired=False) as rsps:
        yield rsps
