"""run-it: power parsing, impact conversions with provenance, and measured energy of a launched command."""
import plistlib
import sys
import time
import unittest

from run_it import impact, power
from run_it.sampler import Child, Sampler

WRAP = 2**64


def ioreg(**battery):
    return plistlib.dumps([battery])


class PowerParsingTest(unittest.TestCase):
    def test_charger_uses_smc_system_load(self):
        plist = ioreg(ExternalConnected=True, PowerTelemetryData={"SystemLoad": 20931}, Voltage=13070,
                      InstantAmperage=0)
        self.assertAlmostEqual(power.parse_system_watts(plist), 20.931)

    def test_battery_uses_voltage_times_signed_current(self):
        # On battery SystemLoad is stale/negative and current is a uint64-wrapped negative number.
        plist = ioreg(ExternalConnected=False, PowerTelemetryData={"SystemLoad": WRAP - 1111}, Voltage=13070,
                      InstantAmperage=WRAP - 985)
        self.assertAlmostEqual(power.parse_system_watts(plist), 13.070 * 0.985)

    def test_no_telemetry_is_unknown_not_zero(self):
        self.assertIsNone(power.parse_system_watts(b""))
        self.assertIsNone(power.parse_system_watts(ioreg(ExternalConnected=True)))


class ImpactTest(unittest.TestCase):
    def test_one_kwh_converts_with_the_cited_factors(self):
        self.assertAlmostEqual(impact.co2_grams(impact.J_PER_KWH), impact.GRID.value * 1000)
        self.assertAlmostEqual(impact.water_ml(impact.J_PER_KWH), impact.WATER.value * 1000)
        self.assertIn("CEA", impact.GRID.source)

    def test_env_override_is_labelled_as_such(self):
        import os
        os.environ["RUN_IT_TEST_FACTOR"] = "0.5"
        try:
            factor = impact._factor("RUN_IT_TEST_FACTOR", 0.727, "kgCO2e/kWh", "default source")
        finally:
            del os.environ["RUN_IT_TEST_FACTOR"]
        self.assertEqual((factor.value, factor.source), (0.5, "RUN_IT_TEST_FACTOR override"))

    def test_receipt_tags_every_number_with_its_provenance(self):
        text = impact.receipt("npm start", 134, 412.0, "counters", "7 (2 high)")
        self.assertIn("[measured: counters]", text)
        self.assertIn("[modelled: 0.727", text)
        self.assertIn("[static: impact not measured]", text)
        self.assertIn("[unavailable]", impact.receipt("x", 1, None, "counters", None))


@unittest.skipUnless(sys.platform == "darwin", "per-process energy counters are macOS-only")
class MeasuredRunTest(unittest.TestCase):
    def test_launched_command_energy_is_measured_through_exit(self):
        child = Child([sys.executable, "-c", "import time\nt=time.time()\nwhile time.time()-t<0.6: pass"], ".")
        sampler = Sampler(child)
        deadline = time.monotonic() + 10
        while child.returncode is None and time.monotonic() < deadline:
            sampler.sample()
            time.sleep(0.2)
        self.assertEqual(child.returncode, 0)
        self.assertTrue(sampler.measured)
        self.assertGreater(sampler.app_j, 0.1)  # 0.6 s of one busy core is well over 0.1 J

    def test_stop_ends_the_process_group_and_keeps_final_energy(self):
        child = Child([sys.executable, "-c", "while True: pass"], ".")
        sampler = Sampler(child)
        sampler.sample()
        time.sleep(0.5)
        child.stop()
        sampler.sample()
        self.assertIsNotNone(child.returncode)
        self.assertGreater(sampler.app_j, 0.1)


if __name__ == "__main__":
    unittest.main()
