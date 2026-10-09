"""FE-10: the LCP resource is not discoverable in the initial HTML (client-side rendering), artifact-only."""
from owner_c.web.artifact import flag

KEY = "FE-10"
LANGUAGE = "web"
ARTIFACT_ONLY = True
FIELDS = ("lcp_request_discoverable",)  # an artifact entry without one of these is not evidence for this check
DETECTOR_VERSION = "1.0.0"
PROFILER = "lighthouse"
REFS = [
    "https://raw.githubusercontent.com/GoogleChrome/lighthouse/main/core/audits/insights/lcp-discovery-insight.js",
    "https://developer.chrome.com/docs/performance/insights/lcp-discovery",
]
RECOMMENDATION = ("Put the LCP image in the server-rendered HTML (or preload it) so the browser can discover and fetch it "
                  "before any JavaScript runs; avoid inserting the hero image from client-side code.")
LIMITATION = ("Needs a Lighthouse 13 JSON report from a representative run. The check reads `requestDiscoverable` of "
              "`lcp-discovery-insight` (true when the LCP request is a preload or was found by the HTML parser in the "
              "document), never the audit score. Pages whose LCP is text have no data. The finding is on a `page:` scope; "
              "the cited taxonomy source (web.dev code splitting) does not cover this row, so the evidence is the "
              "Lighthouse insight (decision D14).")


def accepts(path: str) -> bool:
    return False  # page-level evidence only


def evaluate_artifact(data: dict, settings: dict) -> list:
    if flag(data, "lcp_request_discoverable"):
        return []
    observed = [("lcp_request_discoverable", False)]
    if data.get("lcp_selector"):
        observed.append(("lcp_selector", data["lcp_selector"]))
    return [{"anchor": "lcp-not-discoverable", "confidence": "high", "observed": observed,
             "summary": "The LCP image request is not discoverable in the initial document (client-side rendered)."}]
