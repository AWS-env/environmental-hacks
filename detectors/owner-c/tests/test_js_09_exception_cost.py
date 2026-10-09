"""Why JS-09 is static: V8 charges stack-trace capture and GC to native frames (real profile of fixtures/real/node/throwy.js)."""
import json
import unittest

from helpers import FIXTURES
from owner_c.normalize import normalize_all

NODE = FIXTURES / "real" / "node"
THROWY = (NODE / "throwy.js").read_text()


class ExceptionCostIsNotAttributableTests(unittest.TestCase):
    def test_real_exception_heavy_profiles_attribute_nothing_to_the_throwing_function(self):
        cpu = json.loads((NODE / "throwy.cpuprofile").read_text())
        data = normalize_all({"cpuprofile": cpu}, [("throwy.js", THROWY)])
        self.assertNotIn("function_time_share_line_1", data.get("node-cpu", {}).get("throwy.js", {}))


if __name__ == "__main__":
    unittest.main()
