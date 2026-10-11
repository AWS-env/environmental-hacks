import unittest

from scanner.adapters.owner_b import PLAN_CHECKS, OwnerB
from scanner.core import ScanContext
from shared.contracts.validation import validate_pair

DJANGO = "def article_page(page):\n    return Article.objects.filter(live=True)[page * 10:page * 10 + 10]\n"
PRISMA = "export async function listUsers(page: number) {\n  return prisma.user.findMany({ skip: page * 10, take: 10 });\n}\n"


def run_check(check_id, files):
    context = ScanContext("fixture:repo", "b" * 40, "orm-scan", files, {})
    runs = [run for run in OwnerB().run(context) if run.check_id == check_id]
    assert len(runs) == 1, runs
    return runs[0]


def run_db39(files):
    return run_check("DB-39", files)


class OrmStaticScanTest(unittest.TestCase):
    def test_python_and_typescript_offset_pagination_are_reported_with_valid_evidence(self):
        run = run_db39([("app/views.py", DJANGO), ("src/users.ts", PRISMA), ("README.md", "ignored")])
        self.assertIsNone(run.error)
        self.assertEqual(run.result["status"], "completed")
        self.assertEqual({f["identity"] for f in run.result["findings"]}, {"article_page:django.slice", "listUsers:option.skip"})
        self.assertEqual(run.result["measurements"], [])
        validate_pair(run.payload, run.result)

    def test_unparsable_file_makes_coverage_partial_not_clean(self):
        run = run_db39([("app/views.py", DJANGO), ("app/bad.py", "def f(:\n")])
        self.assertEqual(run.result["status"], "partial")
        self.assertTrue(any("bad.py" in text for text in run.result["coverage"]["limitations"]))
        validate_pair(run.payload, run.result)

    def test_no_python_js_ts_files_is_not_applicable(self):
        run = run_db39([("index.php", "<?php echo 1;")])
        self.assertTrue(run.not_applicable and not run.result and not run.error)

    def test_test_files_are_not_examined(self):
        run = run_db39([("tests/test_views.py", DJANGO)])
        self.assertTrue(run.not_applicable)

    def test_javascript_test_files_are_not_examined(self):
        run = run_db39([("src/users.test.ts", PRISMA), ("src/__tests__/users.ts", PRISMA)])
        self.assertTrue(run.not_applicable)

    def test_plan_checks_stay_visible_as_unavailable_on_a_source_only_scan(self):
        for check_id in sorted(PLAN_CHECKS):
            run = run_check(check_id, [("app/views.py", DJANGO)])
            self.assertTrue(run.unavailable and not run.result and not run.error)
            self.assertIn("EXPLAIN", run.unavailable)
