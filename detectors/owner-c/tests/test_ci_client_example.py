"""The client upload example (examples/client-ci/upload-ci-history.sh) with fake `aws` and `curl` (no network).

It must upload repo.zip and ci_history.json first and manifest.json LAST (the manifest upload triggers the parser),
must never print a presigned URL, and must stop on a failed upload.
"""
import json
import os
import pathlib
import shutil
import stat
import subprocess
import tempfile
import unittest

from helpers_ci import SHA

SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "examples" / "client-ci" / "upload-ci-history.sh"
BASH = shutil.which("bash")

FAKE_AWS = """#!/usr/bin/env bash
# fake `aws lambda invoke`: the output path is the last argument
out="${@: -1}"
printf '%s' '{"urls": {"repo": {"url": "https://s3.example/SECRET-repo"}, "ci_history": {"url": "https://s3.example/SECRET-hist"}, "manifest": {"url": "https://s3.example/SECRET-manifest"}}}' > "$out"
echo "$@" >> "$FAKE_LOG"
"""
FAKE_CURL = """#!/usr/bin/env bash
for a in "$@"; do last="$a"; done
echo "PUT $last" >> "$FAKE_LOG"
for a in "$@"; do case "$a" in @*) case "$last" in *SECRET-repo*) cp "${a#@}" "$FAKE_LOG.repo.zip";; esac;; esac; done
case "$last" in *SECRET-hist*) [ -n "${FAKE_FAIL_HISTORY:-}" ] && { printf 500; exit 0; };; esac
printf 200
"""


@unittest.skipUnless(BASH, "needs bash")
class ClientUploadExample(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        for name, body in (("aws", FAKE_AWS), ("curl", FAKE_CURL)):
            path = self.bin / name
            path.write_text(body, encoding="utf-8", newline="\n")
            path.chmod(path.stat().st_mode | stat.S_IEXEC)
        self.repo = self.tmp / "repo"
        (self.repo / ".github/workflows").mkdir(parents=True)
        (self.repo / ".github/workflows/ci.yml").write_text("name: CI\non: push\njobs: {}\n", encoding="utf-8")
        (self.repo / "node_modules").mkdir()
        (self.repo / "node_modules/skip.js").write_text("x", encoding="utf-8")
        self.history = self.tmp / "history.json"
        self.history.write_text(json.dumps({"schema": "owner-c.github-actions-history-bundle.v1", "workflows": []}), encoding="utf-8")
        self.log = self.tmp / "log.txt"

    def run_script(self, **env):
        full = {**os.environ, "PATH": f"{self.bin}{os.pathsep}{os.environ['PATH']}", "FAKE_LOG": str(self.log),
                "CI_HISTORY_JSON": str(self.history), **env}
        return subprocess.run([BASH, str(SCRIPT), "github:o/r", SHA, str(self.repo), "ci.yml"],
                              capture_output=True, text=True, env=full)

    def test_uploads_the_repo_and_history_first_and_the_manifest_last_without_printing_urls(self):
        done = self.run_script()
        self.assertEqual(done.returncode, 0, done.stderr)
        puts = [line.split(" ", 1)[1] for line in self.log.read_text().splitlines() if line.startswith("PUT ")]
        self.assertEqual([p.rsplit("-", 1)[1] for p in puts], ["repo", "hist", "manifest"])
        self.assertNotIn("SECRET", done.stdout + done.stderr)
        self.assertIn("uploaded manifest: HTTP 200", done.stdout)
        invoked = next(line for line in self.log.read_text().splitlines() if "owner-c-presign" in line)
        self.assertIn("ci_history", invoked)  # the presign request names the artifact type

    def test_the_repo_zip_has_one_top_folder_so_the_parser_keeps_workflow_paths(self):
        """The parser drops the first path component (GitHub archives have `<repo>-<sha>/`). A flat zip lost `.github/` and
        the CI checks never saw the workflow (found by the live AWS test, 2026-10-10)."""
        from owner_c.aws.common import iter_zip
        self.assertEqual(self.run_script().returncode, 0)
        paths = [p for p, _ in iter_zip(pathlib.Path(str(self.log) + ".repo.zip").read_bytes())]
        self.assertEqual(paths, [".github/workflows/ci.yml"])

    def test_a_failed_upload_stops_before_the_manifest(self):
        done = self.run_script(FAKE_FAIL_HISTORY="1")
        self.assertNotEqual(done.returncode, 0)
        puts = [line for line in self.log.read_text().splitlines() if line.startswith("PUT ")]
        self.assertFalse([p for p in puts if "manifest" in p], "the manifest triggers the parser, so it must not be sent")

    def test_it_asks_for_a_workflow_name(self):
        done = subprocess.run([BASH, str(SCRIPT), "github:o/r", SHA, str(self.repo)], capture_output=True, text=True)
        self.assertNotEqual(done.returncode, 0)


if __name__ == "__main__":
    unittest.main()
