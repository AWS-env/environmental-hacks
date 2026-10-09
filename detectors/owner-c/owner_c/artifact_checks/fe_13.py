"""FE-13: unused CSS, reported only from a client-produced Lighthouse 13 report (artifact-only)."""
from owner_c.web.artifact import number

KEY = "FE-13"
LANGUAGE = "web"
ARTIFACT_ONLY = True
FIELDS = ("unused_css_bytes",)  # an artifact entry without one of these is not evidence for this check
DETECTOR_VERSION = "1.0.0"
PROFILER = "lighthouse"
SETTINGS = {"min_unused_bytes": (0, None)}  # (exclusive minimum, no maximum)
REFS = [
    "https://web.dev/articles/defer-non-critical-css",
    "https://raw.githubusercontent.com/GoogleChrome/lighthouse/main/core/audits/byte-efficiency/unused-css-rules.js",
]
RECOMMENDATION = ("Ship only the CSS a page needs: split the stylesheet per page or component, remove dead rules, and "
                  "inline the critical subset.")
LIMITATION = ("Needs a Lighthouse 13 JSON report from a representative run. Coverage reflects one page load: rules used "
              "on other pages or after interaction count as unused, so a shared stylesheet can look mostly unused. A "
              "stylesheet that the report does not list has no evidence and is left out of coverage. No source line is "
              "cited.")


def accepts(path: str) -> bool:
    return path.lower().endswith(".css")


def evaluate_artifact(data: dict, settings: dict) -> list:
    unused = number(data, "unused_css_bytes")
    if unused < settings["min_unused_bytes"]:
        return []
    observed = [("unused_css_bytes", unused)]
    for extra in ("unused_css_percent", "total_css_bytes"):
        if isinstance(data.get(extra), (int, float)) and not isinstance(data[extra], bool):
            observed.append((extra, data[extra]))
    return [{"anchor": "unused-css", "confidence": "medium", "observed": observed,
             "summary": f"{unused:,.0f} bytes of this stylesheet are unused on the page Lighthouse loaded."}]
