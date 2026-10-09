"""FE-02: the LCP image is lazy-loaded, reported only from a client-produced Lighthouse 13 report (artifact-only)."""
from owner_c.web.artifact import flag

KEY = "FE-02"
LANGUAGE = "web"
ARTIFACT_ONLY = True
FIELDS = ("lcp_eagerly_loaded",)  # an artifact entry without one of these is not evidence for this check
DETECTOR_VERSION = "1.0.0"
PROFILER = "lighthouse"
REFS = [
    "https://web.dev/articles/browser-level-image-lazy-loading",
    "https://raw.githubusercontent.com/GoogleChrome/lighthouse/main/core/audits/insights/lcp-discovery-insight.js",
]
RECOMMENDATION = ("Remove `loading=\"lazy\"` from the image that is the Largest Contentful Paint element (the hero or "
                  "first visible image) and consider `fetchpriority=\"high\"`; lazy-load only images below the fold.")
LIMITATION = ("Needs a Lighthouse 13 JSON report from a representative run; the LCP element can differ by viewport, so one "
              "run covers one layout. The check reads the `eagerlyLoaded` checklist value of `lcp-discovery-insight`, never "
              "the audit score (the score is 0 whenever `fetchpriority=high` is missing, lazy or not). Pages whose LCP is "
              "text or not applicable have no data. The finding is on a `page:` scope because the report names a DOM "
              "selector, not a source file.")


def accepts(path: str) -> bool:
    return False  # page-level evidence only


def evaluate_artifact(data: dict, settings: dict) -> list:
    if flag(data, "lcp_eagerly_loaded"):
        return []
    observed = [("lcp_eagerly_loaded", False)]
    if data.get("lcp_selector"):
        observed.append(("lcp_selector", data["lcp_selector"]))
    return [{"anchor": "lcp-lazy-loaded", "confidence": "high", "observed": observed,
             "summary": "The Largest Contentful Paint image is lazy-loaded (`loading=\"lazy\"`), which delays it."}]
