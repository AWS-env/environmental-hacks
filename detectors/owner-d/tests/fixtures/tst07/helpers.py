# Synthetic TST-07 fixture: production module, not a test module. Never executed.


def test_connection(url):
    return url.startswith("https://")
