"""Every Owner D check with required context settings ships its README reference values (issue #441).

Repository scans (scanner/adapters/owner_d.py) put a module's `REFERENCE_SETTINGS` into the
contract context, so a check whose README documents required settings must expose exactly the
README's reference values, or scans report it `unavailable`. Telemetry checks are never run by a
repository scan; their reference values are the AWS telemetry route's defaults (owner_d/aws/registry.py).
"""

import json
import re
import sys
import unittest
from pathlib import Path

DETECTOR_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(DETECTOR_DIR))
sys.path.insert(0, str(REPO_ROOT))

from owner_d import cli
from owner_d.aws import registry
from scanner.adapters.owner_d import REPO_DERIVED

README = DETECTOR_DIR / "README.md"
SECTION = re.compile(r"^## (?P<check>[A-Z]+-\d+) ")
ROW = re.compile(r"^\| `(?P<key>[a-z_]+)` \|.*\| (?P<reference>[^|]+) \|$")
# Checks known to need settings; guards against a README format change silently emptying the test.
KNOWN = {"INF-01", "INF-02", "LLM-05", "LLM-10", "LLM-15", "OBS-06", "OBS-07", "OBS-18", "TST-03", "TST-07", "TST-12"}


def required_settings():
    """{check_id: {key: reference value, or None when it is repository-specific}} from non-optional
    "### Context settings" tables in the README."""
    found, check, in_settings = {}, None, False
    for line in README.read_text(encoding="utf-8").splitlines():
        section = SECTION.match(line)
        if section or line.startswith("## "):
            check, in_settings = (section["check"] if section else None), False
        elif line.startswith("### "):
            in_settings = check is not None and line.startswith("### Context settings") and "optional" not in line
        elif in_settings and (row := ROW.match(line)):
            cell = row["reference"].strip()
            literal = re.match(r"^`([^`]+)`", cell)
            found.setdefault(check, {})[row["key"]] = json.loads(literal[1]) if literal else None
    return found


class ReferenceSettingsTest(unittest.TestCase):
    def setUp(self):
        self.required = required_settings()
        self.telemetry = {check.check_id: check.defaults for check in registry.CHECKS}

    def test_readme_tables_are_found(self):
        self.assertLessEqual(KNOWN, set(self.required))

    def test_every_check_with_required_settings_exposes_the_readme_reference_values(self):
        for check_id, settings in sorted(self.required.items()):
            module = cli.DETECTORS.get(check_id)
            if module is None:  # documented but not registered: nothing to scan yet
                continue
            with self.subTest(check_id):
                fixed = {key: value for key, value in settings.items() if value is not None}
                repo_specific = {key for key, value in settings.items() if value is None}
                self.assertLessEqual(repo_specific, set(REPO_DERIVED.get(check_id, {})),
                                     f"{check_id}: no fixed README reference; the scanner must derive it")
                if getattr(module, "SUPPORTED_KIND", "static") == "telemetry":
                    self.assertEqual(self.telemetry.get(check_id), fixed)
                    continue
                self.assertTrue(hasattr(module, "REFERENCE_SETTINGS"),
                                f"{module.__name__} documents required settings but has no REFERENCE_SETTINGS")
                self.assertEqual(module.REFERENCE_SETTINGS, fixed)


if __name__ == "__main__":
    unittest.main()
