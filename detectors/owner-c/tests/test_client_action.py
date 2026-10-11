"""The client action's script (.github/actions/owner-c-profile-upload/profile_upload.py), with the tools and AWS faked.

It must upload repo.zip and every produced artifact first and manifest.json LAST, upload only artifacts that were
produced, skip (never fail) a tool that cannot run, never print a presigned URL, and keep secrets out of repo.zip.
"""
import contextlib
import importlib.util
import io
import json
import pathlib
import tempfile
import unittest
import zipfile
from unittest import mock

SCRIPT = pathlib.Path(__file__).resolve().parents[3] / ".github" / "actions" / "owner-c-profile-upload" / "profile_upload.py"
spec = importlib.util.spec_from_file_location("profile_upload", SCRIPT)
pu = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pu)

SHA = "a" * 40
SECRET = "https://s3.example/SECRET-"


def fake_urls(artifacts):
    return {name: {"key": name, "url": SECRET + name} for name in ["repo", *artifacts, "manifest"]}


class Harness:
    """Runs main() in a temp repo with collectors and presign/put replaced; records what was uploaded."""

    def __init__(self, tool_output=None, fail=()):
        self.tool_output = tool_output or {}
        self.fail = set(fail)
        self.uploaded = []
        self.zip_names = []

    def collector(self, name, out, args, repo):
        if name in self.fail:
            raise RuntimeError(f"{name} cannot run here")
        if name in self.tool_output:
            out.write_text(self.tool_output[name], encoding="utf-8")

    def put(self, url, path):
        self.uploaded.append(url.removeprefix(SECRET))
        if url.endswith("repo"):
            self.zip_names = zipfile.ZipFile(path).namelist()
        return 200

    def run(self, argv, repo):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(pu, "collect", self.collector), \
                mock.patch.object(pu, "presign", lambda rid, sha, arts, region, work: fake_urls(arts)), \
                mock.patch.object(pu, "put", self.put), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = pu.main(["--repository-id", "github:o/r", "--commit-sha", SHA, "--repo-dir", str(repo), *argv])
        return code, out.getvalue() + err.getvalue()


class ClientActionTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = pathlib.Path(self._tmp.name)
        (self.repo / "app.py").write_text("print(1)\n")
        (self.repo / ".env").write_text("TOKEN=secret\n")
        (self.repo / ".env.local").write_text("TOKEN=secret\n")
        (self.repo / "node_modules").mkdir()
        (self.repo / "node_modules" / "x.js").write_text("x")
        (self.repo / ".git").mkdir()
        (self.repo / ".git" / "config").write_text("c")
        self.addCleanup(self._tmp.cleanup)

    def test_uploads_repo_then_artifacts_then_manifest_last(self):
        h = Harness(tool_output={"speedscope": "{}", "memray_stats": "{}"})
        code, _ = h.run(["--artifacts", "speedscope,memray_stats"], self.repo)
        self.assertEqual(code, 0)
        self.assertEqual(h.uploaded, ["repo", "speedscope", "memray_stats", "manifest"])

    def test_repo_zip_has_one_top_folder_and_no_secrets_or_vcs(self):
        h = Harness(tool_output={"lighthouse": "{}"})
        h.run(["--artifacts", "lighthouse"], self.repo)
        self.assertEqual(h.zip_names, ["repo/app.py"])

    def test_tool_that_cannot_run_is_skipped_not_uploaded(self):
        h = Harness(tool_output={"speedscope": "{}"}, fail={"memray_stats"})
        code, out = h.run(["--artifacts", "speedscope,memray_stats"], self.repo)
        self.assertEqual(code, 0)
        self.assertEqual(h.uploaded, ["repo", "speedscope", "manifest"])
        self.assertIn("memray_stats skipped", out)

    def test_nothing_produced_uploads_nothing(self):
        h = Harness(fail={"speedscope"})
        code, out = h.run(["--artifacts", "speedscope"], self.repo)
        self.assertEqual((code, h.uploaded), (0, []))
        self.assertIn("nothing was uploaded", out)

    def test_empty_or_invalid_output_is_not_uploaded(self):
        h = Harness(tool_output={"speedscope": "", "lighthouse": "not json"})
        h.run(["--artifacts", "speedscope,lighthouse"], self.repo)
        self.assertEqual(h.uploaded, [])

    def test_unknown_artifact_is_rejected(self):
        h = Harness()
        code, _ = h.run(["--artifacts", "xray"], self.repo)  # X-Ray is read from the client's account, never uploaded
        self.assertEqual((code, h.uploaded), (2, []))

    def test_never_prints_presigned_url_even_when_upload_fails(self):
        h = Harness(tool_output={"speedscope": "{}"})

        def boom(url, path):
            raise OSError(f"cannot reach {url}")
        h.put = boom
        code, out = h.run(["--artifacts", "speedscope"], self.repo)
        self.assertEqual(code, 1)
        self.assertNotIn("SECRET", out)

    def test_manifest_matches_what_was_produced(self):
        captured = {}
        real_write = pathlib.Path.write_text

        def spy(self_, data, *a, **k):
            if self_.name == "manifest.json":
                captured.update(json.loads(data))
            return real_write(self_, data, *a, **k)
        h = Harness(tool_output={"speedscope": "{}"}, fail={"lighthouse"})
        with mock.patch.object(pathlib.Path, "write_text", spy):
            h.run(["--artifacts", "speedscope,lighthouse"], self.repo)
        self.assertEqual(captured, {"repository_id": "github:o/r", "commit_sha": SHA, "artifacts": ["speedscope"]})


class CollectorGuards(unittest.TestCase):
    def args(self, **kw):
        base = dict(python_args="", node_args="", lighthouse_url="", ci_workflows="", ci_repo="", repository_id="github:o/r")
        base.update(kw)
        return mock.Mock(**base)

    def test_python_tools_need_python_args(self):
        with self.assertRaises(RuntimeError):
            pu.collect("speedscope", pathlib.Path("x"), self.args(), pathlib.Path("."))

    def test_lighthouse_needs_url(self):
        with self.assertRaises(RuntimeError):
            pu.collect("lighthouse", pathlib.Path("x"), self.args(), pathlib.Path("."))

    def test_memray_refuses_windows(self):
        with mock.patch.object(pu.sys, "platform", "win32"), self.assertRaises(RuntimeError):
            pu.collect_memray_stats(pathlib.Path("x"), ["app.py"], pathlib.Path("."))

    def test_pyspy_command_has_no_nonblocking_and_wraps_python(self):
        calls = []
        with mock.patch.object(pu, "pip_install"), mock.patch.object(pu, "installed_tool", lambda n: n), \
                mock.patch.object(pu, "run", lambda cmd, **kw: calls.append(cmd)):
            pu.collect_speedscope(pathlib.Path("o.json"), ["-m", "pytest"], pathlib.Path("."))
        cmd = calls[0]
        self.assertEqual(cmd[:2], ["py-spy", "record"])  # an executable, not `python -m py_spy`
        self.assertNotIn("--nonblocking", cmd)
        self.assertEqual(cmd[cmd.index("--") + 2:], ["-m", "pytest"])
        self.assertIn("--format", cmd)


class JsAndCiArtifacts(unittest.TestCase):
    def args(self, **kw):
        base = dict(python_args="", node_args="", lighthouse_url="", ci_workflows="", ci_repo="", repository_id="github:o/r")
        base.update(kw)
        return mock.Mock(**base)

    def test_all_six_new_and_old_types_are_known(self):
        self.assertEqual(set(pu.KNOWN), {"speedscope", "memray_stats", "lighthouse", "cpuprofile", "heapprofile", "ci_history"})

    def test_node_tools_need_node_args(self):
        for name in ("cpuprofile", "heapprofile"):
            with self.assertRaises(RuntimeError):
                pu.collect(name, pathlib.Path("x"), self.args(), pathlib.Path("."))

    def test_ci_history_needs_workflows(self):
        with self.assertRaises(RuntimeError):
            pu.collect("ci_history", pathlib.Path("x"), self.args(), pathlib.Path("."))

    def test_cpuprofile_command_and_largest_profile_is_picked(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = pathlib.Path(tmp) / "cpuprofile.json"
            calls = []

            def fake_run(cmd, **kw):
                calls.append(cmd)
                d = pathlib.Path(tmp) / "cpu"
                d.mkdir()
                (d / "small.cpuprofile").write_text("{}")
                (d / "main.cpuprofile").write_text('{"nodes": []}')
            with mock.patch.object(pu, "run", fake_run):
                pu.collect_cpuprofile(out, ["app.js"], pathlib.Path("."))
            self.assertEqual(calls[0][:3], ["node", "--no-opt", "--cpu-prof"])
            self.assertEqual(out.read_text(), '{"nodes": []}')

    def test_cpuprofile_without_output_is_an_error(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(pu, "run", lambda cmd, **kw: None):
            with self.assertRaises(RuntimeError):
                pu.collect_cpuprofile(pathlib.Path(tmp) / "cpuprofile.json", ["app.js"], pathlib.Path("."))

    def test_heapprofile_uses_the_collector_and_heap_out(self):
        seen = {}
        with mock.patch.object(pu, "run", lambda cmd, **kw: seen.update(cmd=cmd, env=kw["env"])):
            pu.collect_heapprofile(pathlib.Path("h.json"), ["app.js"], pathlib.Path("."))
        self.assertIn(str(pu.HEAP_COLLECTOR), seen["cmd"])
        self.assertEqual(pathlib.Path(seen["env"]["HEAP_OUT"]), pathlib.Path("h.json"))
        self.assertTrue(pu.HEAP_COLLECTOR.is_file())  # shipped next to the action

    def test_ci_history_command_names_the_repo_and_each_workflow(self):
        seen = {}
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(pu, "run", lambda cmd, **kw: seen.update(cmd=cmd)):
            pu.collect_ci_history(pathlib.Path(tmp) / "ci.json", "org/repo", ["ci.yml", "release.yml"])
        cmd = seen["cmd"]
        self.assertEqual(cmd[cmd.index("--repo") + 1], "org/repo")
        self.assertEqual([cmd[i + 1] for i, c in enumerate(cmd) if c == "--workflow"], ["ci.yml", "release.yml"])
        self.assertTrue(pu.CI_COLLECTOR.is_file())

    def test_ci_repo_overrides_the_repository_id_and_defaults_to_it(self):
        seen = []
        with mock.patch.object(pu, "collect_ci_history", lambda out, repo, wfs: seen.append(repo)):
            pu.collect("ci_history", pathlib.Path("x"), self.args(ci_workflows="ci.yml"), pathlib.Path("."))
            pu.collect("ci_history", pathlib.Path("x"), self.args(ci_workflows="ci.yml", ci_repo="real/repo"), pathlib.Path("."))
        self.assertEqual(seen, ["o/r", "real/repo"])

    def test_all_six_upload_in_order_with_manifest_last(self):
        names = ["speedscope", "memray_stats", "lighthouse", "cpuprofile", "heapprofile", "ci_history"]
        h = Harness(tool_output={n: "{}" for n in names})
        with tempfile.TemporaryDirectory() as tmp:
            repo = pathlib.Path(tmp)
            (repo / "app.py").write_text("x")
            code, _ = h.run(["--artifacts", ",".join(names)], repo)
        self.assertEqual((code, h.uploaded), (0, ["repo", *names, "manifest"]))


if __name__ == "__main__":
    unittest.main()
