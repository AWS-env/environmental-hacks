# Synthetic TST-10 fixture: production module, not a test module. Never executed.


def test_connection(url):
    return url.startswith("https://")
