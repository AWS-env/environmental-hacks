# Synthetic TST-02 fixture: production module, not a test module. Never executed.
from shop.cart import total


def test_connection(url):
    return total([url])


def test_ready(url):
    return total([url, url])
