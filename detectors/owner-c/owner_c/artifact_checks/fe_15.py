"""FE-15: forced synchronous layout, reported only from a client-produced Lighthouse 13 report (artifact-only)."""
from owner_c.web.artifact import number

KEY = "FE-15"
LANGUAGE = "web"
ARTIFACT_ONLY = True
FIELDS = ("forced_reflow_ms",)  # an artifact entry without one of these is not evidence for this check
DETECTOR_VERSION = "1.0.0"
PROFILER = "lighthouse"
SETTINGS = {"min_reflow_ms": (0, None)}  # (exclusive minimum, no maximum)
SUFFIXES = (".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx")
REFS = [
    "https://web.dev/articles/avoid-large-complex-layouts-and-layout-thrashing",
    "https://developer.chrome.com/docs/performance/insights/forced-reflow",
]
RECOMMENDATION = ("Batch DOM reads before writes: read layout values (offsetHeight, getBoundingClientRect, ...) once, "
                  "cache them, then write styles; avoid alternating reads and writes inside loops.")
LIMITATION = ("Needs a Lighthouse 13 JSON report from a representative run. `forced-reflow-insight` fails on any forced "
              "reflow with no duration limit, so `min_reflow_ms` (default 30, the DevTools insight's reporting level) is "
              "declared in `context`. Reflow time is summed per script from the first (top function) table; unattributed "
              "time is ignored. A script the report does not list has no evidence and is left out of coverage. No source "
              "line is cited; a bundled script not in the repo is reported on a `page:` scope.")


def accepts(path: str) -> bool:
    lowered = path.lower()
    return lowered.endswith(SUFFIXES) and ".min." not in lowered


def evaluate_artifact(data: dict, settings: dict) -> list:
    reflow = number(data, "forced_reflow_ms")
    if reflow < settings["min_reflow_ms"]:
        return []
    return [{"anchor": "forced-reflow", "confidence": "medium", "observed": [("forced_reflow_ms", reflow)],
             "summary": f"This script forces synchronous layout for {reflow:,.1f} ms in the Lighthouse report."}]
