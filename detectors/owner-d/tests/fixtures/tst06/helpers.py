# Synthetic TST-06 fixture: production module, not a test module. Never executed.


def test_connection(url):
    return url.startswith("https://")
