"""Node discovery for the owner A/B adapters: NODE_BINARY first, then `node` on PATH (local CLI)."""
import os
import shutil
import stat
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from scanner.adapters import node
from scanner.core import AdapterUnavailable


class NodeDiscoveryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.exe = self.tmp / "node"
        self.exe.write_text("#!/bin/sh\n")
        self.exe.chmod(self.exe.stat().st_mode | stat.S_IXUSR)
        self.entry = self.tmp / "index.js"
        self.entry.write_text("")

    def env(self, **values):
        env = {k: v for k, v in os.environ.items() if k != "NODE_BINARY"}
        return mock.patch.dict(os.environ, {**env, **values}, clear=True)

    def test_node_binary_takes_precedence_over_path(self):
        with self.env(NODE_BINARY=str(self.exe)), mock.patch.object(node.shutil, "which", return_value="/usr/bin/node"):
            self.assertEqual(node.find_node(), str(self.exe))

    def test_falls_back_to_path_when_node_binary_is_unset_or_empty(self):
        for env in ({}, {"NODE_BINARY": ""}):
            with self.env(**env), mock.patch.object(node.shutil, "which", return_value="/usr/bin/node") as which:
                self.assertEqual(node.find_node(), "/usr/bin/node")
            which.assert_called_once_with("node")

    def test_unusable_node_binary_is_unavailable_not_silently_replaced(self):
        plain = self.tmp / "not-executable"
        plain.write_text("")
        for bad in (str(self.tmp / "missing"), str(plain), str(self.tmp)):
            with self.env(NODE_BINARY=bad), mock.patch.object(node.shutil, "which", return_value="/usr/bin/node"):
                self.assertIsNone(node.find_node())
                with self.assertRaisesRegex(AdapterUnavailable, "NODE_BINARY=.* is not an executable file"):
                    node.call("owner-a", self.entry, "list")

    def test_no_node_anywhere_is_unavailable(self):
        with self.env(), mock.patch.object(node.shutil, "which", return_value=None):
            with self.assertRaisesRegex(AdapterUnavailable, "not on PATH"):
                node.call("owner-a", self.entry, "list")

    def test_call_runs_the_driver_with_the_discovered_binary(self):
        done = SimpleNamespace(returncode=0, stdout='{"checks": []}', stderr="")
        with self.env(NODE_BINARY=str(self.exe)), mock.patch.object(node.subprocess, "run", return_value=done) as run:
            self.assertEqual(node.call("owner-b", self.entry, "list"), {"checks": []})
        self.assertEqual(run.call_args.args[0], [str(self.exe), str(node.DRIVER), "owner-b", str(self.entry), "list"])


if __name__ == "__main__":
    unittest.main()
