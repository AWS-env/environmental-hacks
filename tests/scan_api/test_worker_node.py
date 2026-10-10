"""The worker runs owners A and B only when it finds Node.js (the stack's node-runtime layer)."""
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PROBE = "from scan_api import worker; print(','.join(sorted(worker.LAMBDA_OWNERS)))"


def lambda_owners(node_binary):
    """LAMBDA_OWNERS as a fresh worker import computes it (the worker looks for Node once, at cold start)."""
    env = {**os.environ, "NODE_BINARY": node_binary, "SCAN_BUCKET": "bucket", "AWS_DEFAULT_REGION": "ap-south-1"}
    return subprocess.run([sys.executable, "-c", PROBE], cwd=ROOT, env=env, capture_output=True, text=True,
                          check=True).stdout.strip()


class WorkerNodeTest(unittest.TestCase):
    def test_owners_a_and_b_run_when_node_is_found(self):
        with tempfile.TemporaryDirectory() as tmp:
            exe = Path(tmp) / "node"
            exe.write_text("#!/bin/sh\n")
            exe.chmod(exe.stat().st_mode | stat.S_IXUSR)
            self.assertEqual(lambda_owners(str(exe)), "A,B,C,D")

    def test_owners_a_and_b_stay_unavailable_without_node(self):
        self.assertEqual(lambda_owners("/nonexistent/bin/node"), "C,D")


if __name__ == "__main__":
    unittest.main()
