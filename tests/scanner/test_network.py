import unittest
from pathlib import Path
from scanner.adapters.owner_b import OwnerB
from scanner.core import ScanContext


class NetworkRepositoryScanTest(unittest.TestCase):
    def test_registered_network_checks_are_visible_but_source_only_scan_cannot_confirm_them(self):
        context = ScanContext('fixture:repo', 'b' * 40, 'source-only',
                              [('app.js', 'const payload = JSON.stringify({a: 1});')], {})
        runs = OwnerB().run(context)
        network = [run for run in runs if run.check_id.startswith('NET-')]
        # Each stacked issue branch must work before later checks are added.
        # Discover implemented modules independently of the exported registry so
        # a missing registration still fails this integration assertion.
        checks = Path(__file__).resolve().parents[2] / 'detectors/owner-b/checks'
        expected = {module.parent.name.upper() for module in checks.glob('net-*/index.js')}
        self.assertTrue(expected, 'at least one network check must be implemented')
        self.assertEqual({run.check_id for run in network}, expected)
        self.assertTrue(all(run.unavailable and not run.result and not run.error for run in network))
        self.assertTrue(all('no runtime network waste is confirmed' in run.unavailable for run in network))


if __name__ == '__main__':
    unittest.main()
