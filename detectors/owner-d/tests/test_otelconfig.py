"""Unit tests for the shared OpenTelemetry Collector config reader (`owner_d/otelconfig.py`)."""

import sys
import unittest
from pathlib import Path

DETECTOR_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DETECTOR_DIR))

from owner_d import miniyaml, otelconfig

CONFIG = """\
receivers:
  otlp:
    protocols:
      grpc:
        endpoint: ${env:MY_POD_IP}:4317
processors:
  batch:
  batch/2:
    timeout: 10s
exporters:
  otlp/backend:
    endpoint: ${env:BACKEND}
connectors:
  forward:
service:
  extensions: []
  pipelines:
    traces:
      receivers: [otlp]
      processors: [batch]
      exporters: [otlp/backend, forward]
    traces/2:
      receivers: [forward]
      processors: ${env:PROCESSORS}
      exporters:
        - otlp/backend
"""


def configs(text):
    return otelconfig.find_configs(miniyaml.load_all(text), text.splitlines())


class ComponentTests(unittest.TestCase):
    def test_component_ids_types_and_lines(self):
        [config] = configs(CONFIG)
        self.assertEqual(config.label, "")
        self.assertEqual(list(config.sections["processors"]), ["batch", "batch/2"])
        exporter = config.component("exporters", "otlp/backend")
        self.assertEqual((exporter.type, exporter.line), ("otlp", 11))
        self.assertIsNone(config.component("exporters", "missing"))
        self.assertEqual(list(config.sections["connectors"]), ["forward"])
        self.assertEqual(config.sections["extensions"], {})
        self.assertEqual(otelconfig.component_type("batch/2"), "batch")
        self.assertEqual(otelconfig.component_type("otlp_http"), "otlp_http")

    def test_pipelines_keep_lists_lines_and_unknown_values(self):
        [config] = configs(CONFIG)
        traces, second = config.pipelines["traces"], config.pipelines["traces/2"]
        self.assertEqual((traces.signal, traces.line, traces.end_line), ("traces", 18, 21))
        self.assertEqual(traces.receivers, ("otlp",))
        self.assertEqual(traces.processors, ("batch",))
        self.assertEqual(traces.exporters, ("otlp/backend", "forward"))
        self.assertEqual((second.signal, second.line, second.end_line), ("traces", 22, 26))
        self.assertIsNone(second.processors)  # ${env:...} instead of a list: unknown, not empty
        self.assertEqual(second.exporters, ("otlp/backend",))

    def test_env_references_are_left_unresolved(self):
        [config] = configs(CONFIG)
        endpoint = otelconfig.text(otelconfig.get(config.component("exporters", "otlp/backend").node, "endpoint"))
        self.assertEqual(endpoint, "${env:BACKEND}")
        for value, unresolved in (("${env:BACKEND}", True), ("${BACKEND}", True), ("$BACKEND", True),
                                  ("${file:/etc/endpoint}", True), ("backend:4317", False), (None, False)):
            with self.subTest(value=value):
                self.assertEqual(otelconfig.is_unresolved(value), unresolved)

    def test_missing_pipelines_is_not_a_config(self):
        self.assertEqual(configs("exporters:\n  otlp: {}\nservice:\n  extensions: [health_check]\n"), [])
        self.assertEqual(configs("- a\n- b\n"), [])

    def test_prefilter_needs_exporters_and_pipelines(self):
        self.assertTrue(otelconfig.might_contain_config(CONFIG))
        self.assertFalse(otelconfig.might_contain_config("receivers:\n  - name: alertmanager\n"))
        self.assertFalse(otelconfig.might_contain_config("exporters:\n  otlp: {}\n"))


class EmbeddedConfigTests(unittest.TestCase):
    def test_operator_resource_and_configmap_keep_file_lines(self):
        text = (
            "kind: OpenTelemetryCollector\nmetadata:\n  name: gw\nspec:\n  config: |\n"
            + "".join(f"    {line}\n" for line in CONFIG.splitlines())
            + "---\nkind: ConfigMap\nmetadata:\n  name: cm\ndata:\n  other: x\n  relay.yaml: |-\n"
            + "".join(f"    {line}\n" for line in CONFIG.splitlines())
        )
        resource, configmap = configs(text)
        self.assertEqual(resource.label, "OpenTelemetryCollector/gw:")
        self.assertEqual(configmap.label, "ConfigMap/cm:relay.yaml:")
        lines = text.splitlines()
        for config in (resource, configmap):
            pipeline = config.pipelines["traces"]
            self.assertEqual(lines[pipeline.line - 1].strip(), "traces:")
            self.assertEqual(lines[pipeline.end_line - 1].strip(), "exporters: [otlp/backend, forward]")
            self.assertEqual(lines[config.component("exporters", "otlp/backend").line - 1].strip(), "otlp/backend:")

    def test_mapping_config_in_v1beta1_resource(self):
        text = "kind: OpenTelemetryCollector\nmetadata:\n  name: a\nspec:\n  config:\n" + "".join(
            f"    {line}\n" for line in CONFIG.splitlines())
        [config] = configs(text)
        self.assertEqual(config.pipelines["traces"].line, 23)

    def test_quoted_string_configs_are_skipped_and_broken_blocks_raise(self):
        quoted = 'kind: ConfigMap\nmetadata:\n  name: q\ndata:\n  relay: "exporters: {}\\nservice: {pipelines: {}}"\n'
        self.assertEqual(configs(quoted), [])
        broken = "kind: ConfigMap\nmetadata:\n  name: b\ndata:\n  relay: |\n    exporters: [otlp\n    pipelines:\n"
        with self.assertRaises(miniyaml.YamlError) as caught:
            configs(broken)
        self.assertIn("embedded config ConfigMap/b:relay at line 5", str(caught.exception))

    def test_aliased_nodes_are_shifted_once(self):
        text = (
            "kind: ConfigMap\nmetadata:\n  name: a\ndata:\n  relay: |\n"
            "    exporters:\n      otlp: &settings\n        endpoint: x:4317\n      otlp/2: *settings\n"
            "    service:\n      pipelines:\n        traces:\n          exporters: [otlp, otlp/2]\n"
        )
        [config] = configs(text)
        node = config.component("exporters", "otlp/2").node
        self.assertEqual(node.key_lines["endpoint"], 8)


if __name__ == "__main__":
    unittest.main()
