"""Run the contract suite and fail if no tests were discovered."""

import sys
import unittest
from pathlib import Path


def main():
    root = Path(__file__).resolve().parents[2]
    suite = unittest.defaultTestLoader.discover(str(root / "tests/contracts"))
    if suite.countTestCases() == 0:
        sys.exit("No contract tests discovered")
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)


if __name__ == "__main__":
    main()
