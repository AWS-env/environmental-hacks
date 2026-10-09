"""Real artifact: a Lighthouse 13.5.0 capture of a local test page (fixtures/real/lighthouse-local-hits.json)."""
import json
import unittest

from helpers import FIXTURES, run_check
from owner_c.artifact_checks import ARTIFACT_CHECKS
from owner_c.connector import build_inputs
from owner_c.normalize import normalize_all

REPORT = json.loads((FIXTURES / "real" / "lighthouse-local-hits.json").read_text())
FILES = [("public/style.css", ".a{}\n"), ("public/style2.css", ".b{}\n")]


class LighthouseNormalizerTests(unittest.TestCase):
    def test_maps_stylesheet_urls_to_repo_files_and_reads_both_audits(self):
        out = normalize_all({"lighthouse": REPORT}, FILES)["lighthouse"]
        self.assertEqual(sorted(out), ["page:/app.js", "page:/index.html", "public/style.css", "public/style2.css"])
        data = out["public/style.css"]
        self.assertEqual((data["profiler"], data["lighthouse_version"]), ("lighthouse", "13.5.0"))
        self.assertEqual((data["render_blocking_ms"], data["unused_css_bytes"]), (463, 34943))

    def test_unmatched_urls_become_page_scopes(self):
        out = normalize_all({"lighthouse": REPORT}, [("other/app.css", "")])["lighthouse"]
        self.assertEqual(sorted(out), ["page:/app.js", "page:/index.html", "page:/style.css", "page:/style2.css"])

    def test_ambiguous_match_becomes_a_page_scope(self):
        files = [("a/style.css", ""), ("b/style.css", "")]
        out = normalize_all({"lighthouse": REPORT}, files)["lighthouse"]
        self.assertIn("page:/style.css", out)

    def test_reads_page_wide_audits_and_reflow_per_script(self):
        files = FILES + [("public/app.js", "x();\n")]
        out = normalize_all({"lighthouse": REPORT}, files)["lighthouse"]
        self.assertEqual(out["public/app.js"]["forced_reflow_ms"], 161.075)
        self.assertEqual((out["page:/index.html"]["dom_total_elements"], out["page:/index.html"]["dom_max_children"]), (2007, 500))

    def test_lcp_checklist_from_real_captures(self):
        for name, scope, field, value in (("lighthouse-lcp-lazy.json", "page:/lazy.html", "lcp_eagerly_loaded", False),
                                          ("lighthouse-lcp-csr.json", "page:/csr.html", "lcp_request_discoverable", False)):
            raw = json.loads((FIXTURES / "real" / name).read_text())
            data = normalize_all({"lighthouse": raw}, [])["lighthouse"][scope]
            self.assertIs(data[field], value, name)
            self.assertTrue(data["lcp_selector"].endswith("img#hero"), name)

    def test_entries_without_the_check_fields_build_no_input_for_that_check(self):
        artifacts = normalize_all({"lighthouse": REPORT}, FILES)
        for key in ("FE-02", "FE-10"):  # this report has no LCP image data
            if key not in ARTIFACT_CHECKS:
                continue  # not part of this PR stack state yet
            payloads = build_inputs(repository_id="github:o/r", commit_sha="a" * 40, scan_id="s", files=FILES,
                                    artifacts=artifacts, checks=[key])
            self.assertEqual(payloads, [], key)

    def test_rejects_a_non_lighthouse_report(self):
        with self.assertRaises(ValueError):
            normalize_all({"lighthouse": {"nodes": []}}, FILES)

    def test_end_to_end_fe_11_and_fe_13(self):
        artifacts = normalize_all({"lighthouse": REPORT}, FILES)
        for key, identity in (("FE-11", "render-blocking-css"), ("FE-13", "unused-css")):
            if key not in ARTIFACT_CHECKS:
                continue  # not part of this PR stack state yet
            _payload, result = run_check(key, dict(FILES), artifacts=artifacts)
            self.assertEqual(result["status"], "completed", key)
            self.assertEqual(sorted(f["identity"] for f in result["findings"]), [identity, identity], key)
            self.assertTrue(all(e["kind"] == "artifact" for f in result["findings"] for e in f["evidence"]), key)

    def test_file_without_report_data_is_partial(self):
        files = FILES + [("public/extra.css", ".c{}\n")]
        artifacts = normalize_all({"lighthouse": REPORT}, files)
        _payload, result = run_check("FE-11", dict(files), artifacts=artifacts)
        self.assertEqual(result["status"], "partial")
        self.assertIn("extra.css", " ".join(result["coverage"]["limitations"]))


if __name__ == "__main__":
    unittest.main()
