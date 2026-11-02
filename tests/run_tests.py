"""Discover and run tests, failing explicitly when discovery is empty."""

import sys
import unittest
from pathlib import Path

# Resolve repository root and add it to sys.path for test discovery
repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_root))


def main() -> int:
    suite = unittest.defaultTestLoader.discover(str(Path(__file__).parent))
    count = suite.countTestCases()
    if count == 0:
        print("ERROR: unittest discovery found zero tests.", file=sys.stderr)
        return 2
    print(f"Discovered {count} tests.")
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
