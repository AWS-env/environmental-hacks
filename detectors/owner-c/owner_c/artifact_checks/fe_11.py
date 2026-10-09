"""FE-11: render-blocking CSS, reported only from a client-produced Lighthouse 13 report (artifact-only)."""
from owner_c.web.artifact import number

KEY = "FE-11"
LANGUAGE = "web"
ARTIFACT_ONLY = True
FIELDS = ("render_blocking_ms",)  # an artifact entry without one of these is not evidence for this check
DETECTOR_VERSION = "1.0.0"
PROFILER = "lighthouse"
SETTINGS = {"min_blocking_ms": (0, None)}  # (exclusive minimum, no maximum)
REFS = [
    "https://web.dev/articles/defer-non-critical-css",
    "https://developer.chrome.com/docs/lighthouse/performance/render-blocking-resources",
]
RECOMMENDATION = ("Inline the critical CSS for the first paint and load the rest without blocking (for example with a "
                  "`media` attribute or the preload pattern), or split the stylesheet per page.")
LIMITATION = ("Needs a Lighthouse 13 JSON report from a representative run uploaded by the client's CI; one run is a "
              "sample that depends on throttling and network. A stylesheet that the report does not list has no evidence "
              "and is left out of coverage. The insight does not name the `<link>` element, so no source line is cited. "
              "Scripts in the same insight are ignored.")


def accepts(path: str) -> bool:
    return path.lower().endswith(".css")


def evaluate_artifact(data: dict, settings: dict) -> list:
    blocking_ms = number(data, "render_blocking_ms")
    if blocking_ms < settings["min_blocking_ms"]:
        return []
    observed = [("render_blocking_ms", blocking_ms)]
    if isinstance(data.get("render_blocking_bytes"), (int, float)) and not isinstance(data["render_blocking_bytes"], bool):
        observed.append(("render_blocking_bytes", data["render_blocking_bytes"]))
    return [{"anchor": "render-blocking-css", "confidence": "medium", "observed": observed,
             "summary": f"This stylesheet delays first paint by {blocking_ms:g} ms in the Lighthouse report."}]
