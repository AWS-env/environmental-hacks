# Synthetic TST-04 fixture: production module, not a test module. Never executed.


def test_connection(retries):
    assert retries == 3
    return retries
