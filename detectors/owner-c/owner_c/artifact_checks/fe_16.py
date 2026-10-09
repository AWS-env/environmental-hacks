"""FE-16: very large DOM, reported only from a client-produced Lighthouse 13 report (artifact-only)."""
from owner_c.web.artifact import number

KEY = "FE-16"
LANGUAGE = "web"
ARTIFACT_ONLY = True
FIELDS = ("dom_total_elements",)  # an artifact entry without one of these is not evidence for this check
DETECTOR_VERSION = "1.0.0"
PROFILER = "lighthouse"
SETTINGS = {"max_dom_elements": (0, None)}  # (exclusive minimum, no maximum); reported when the count is above it
REFS = [
    "https://web.dev/articles/avoid-large-complex-layouts-and-layout-thrashing",
    "https://developer.chrome.com/docs/lighthouse/performance/dom-size",
]
RECOMMENDATION = ("Reduce the number of DOM nodes: render only what is visible (virtualize long lists), remove wrapper "
                  "elements, and defer hidden sections; a large DOM raises style and layout cost on every change.")
LIMITATION = ("Needs a Lighthouse 13 JSON report from a representative run. Lighthouse 13's `dom-size-insight` has no "
              "numeric limit (it only turns informative when a layout or style recalculation is long), so "
              "`max_dom_elements` (default 1400, the old Lighthouse error level) is our own default and is declared in "
              "`context`. The count is for the loaded page state; interaction can add nodes. The finding is on a `page:` "
              "scope.")


def accepts(path: str) -> bool:
    return False  # page-level evidence only


def evaluate_artifact(data: dict, settings: dict) -> list:
    total = number(data, "dom_total_elements")
    if total <= settings["max_dom_elements"]:
        return []
    observed = [("dom_total_elements", total)]
    for extra in ("dom_max_depth", "dom_max_children"):
        if isinstance(data.get(extra), (int, float)) and not isinstance(data[extra], bool):
            observed.append((extra, data[extra]))
    return [{"anchor": "large-dom", "confidence": "medium", "observed": observed,
             "summary": f"The page has {total:,.0f} DOM elements, above the {settings['max_dom_elements']:,.0f} limit."}]
