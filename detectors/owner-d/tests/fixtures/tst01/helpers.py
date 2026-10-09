# Synthetic TST-01 fixture: production module, not a test module. Never executed.


def test_connection(url):
    return url.startswith("https://")
