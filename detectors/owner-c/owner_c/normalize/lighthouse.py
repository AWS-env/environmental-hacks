"""Normalize a Lighthouse 13 JSON report (`lighthouse --output=json`) into per-resource contract data.

Output keys are a repository path when the resource URL matches exactly one repository file (matching on a path
boundary), else `page:<url path>`; page-wide audits use `page:<final page url path>`. Values are flat fields (the
contract cites one `data` field per evidence):
    {"profiler": "lighthouse", "lighthouse_version": "13.5.0", "page_url": "...",
     "render_blocking_ms": 463, "render_blocking_bytes": 35272,          # audits["render-blocking-insight"] items
     "unused_css_bytes": 34943, "unused_css_percent": 99.6, "total_css_bytes": 35085,   # audits["unused-css-rules"]
     "forced_reflow_ms": 161.1,                                           # audits["forced-reflow-insight"], per script
     "lcp_priority_hinted": false, "lcp_request_discoverable": true,      # audits["lcp-discovery-insight"] checklist
     "lcp_eagerly_loaded": false, "lcp_selector": "body > img#hero",
     "dom_total_elements": 2007, "dom_max_depth": 5, "dom_max_children": 500}  # audits["dom-size-insight"]

Only audits that are present and applicable contribute; a resource a report does not list gets no entry, so it is never
treated as clean. Lighthouse 12 and older ids (`render-blocking-resources`, `dom-size`) are not read. For
`forced-reflow-insight` the first table (top function call) is summed per script URL.
"""
import urllib.parse

PROFILER = "lighthouse"
_DOM_ROWS = {"Total elements": "dom_total_elements", "DOM depth": "dom_max_depth", "Most children": "dom_max_children"}
_LCP_KEYS = {"priorityHinted": "lcp_priority_hinted", "requestDiscoverable": "lcp_request_discoverable",
             "eagerlyLoaded": "lcp_eagerly_loaded"}


def _url_path(url: str) -> str:
    return urllib.parse.urlparse(url).path.lstrip("/")


def _scope_key(url: str, files) -> str:
    path = _url_path(url)
    matches = [repo for repo, _content in files
               if repo == path or repo.endswith("/" + path) or path.endswith("/" + repo)]
    return matches[0] if len(matches) == 1 and path else f"page:/{path}"


def _details(raw: dict, audit_id: str) -> dict:
    return (raw.get("audits", {}).get(audit_id) or {}).get("details") or {}


def _items(raw: dict, audit_id: str) -> list:
    return [item for item in _details(raw, audit_id).get("items", []) if isinstance(item, dict) and item.get("url")]


def normalize(raw: dict, files) -> dict:
    if not isinstance(raw, dict) or "audits" not in raw:
        raise ValueError("not a Lighthouse JSON report (no `audits`)")
    page_url = raw.get("finalDisplayedUrl") or raw.get("requestedUrl") or ""
    base = {"profiler": PROFILER, "lighthouse_version": raw.get("lighthouseVersion"), "page_url": page_url}
    out = {}

    def entry(key):
        return out.setdefault(key, dict(base))

    for item in _items(raw, "render-blocking-insight"):
        data = entry(_scope_key(item["url"], files))
        data["render_blocking_ms"] = item.get("wastedMs")
        data["render_blocking_bytes"] = item.get("totalBytes")
    for item in _items(raw, "unused-css-rules"):
        data = entry(_scope_key(item["url"], files))
        data["unused_css_bytes"] = item.get("wastedBytes")
        data["unused_css_percent"] = item.get("wastedPercent")
        data["total_css_bytes"] = item.get("totalBytes")

    tables = [t for t in _details(raw, "forced-reflow-insight").get("items", []) if isinstance(t, dict)]
    per_script = {}
    for row in (tables[0].get("items", []) if tables else []):
        source = row.get("source") or {}
        if source.get("type") == "source-location" and source.get("url") and isinstance(row.get("reflowTime"), (int, float)):
            per_script[source["url"]] = per_script.get(source["url"], 0) + row["reflowTime"]
    for url, total in per_script.items():
        entry(_scope_key(url, files))["forced_reflow_ms"] = round(total, 3)

    page_key = f"page:/{_url_path(page_url)}"
    lcp_items = _details(raw, "lcp-discovery-insight").get("items", [])
    checklist = next((i.get("items") for i in lcp_items if isinstance(i, dict) and i.get("type") == "checklist"), None)
    if isinstance(checklist, dict):
        data = entry(page_key)
        for key, field in _LCP_KEYS.items():
            if isinstance(checklist.get(key), dict):
                data[field] = checklist[key].get("value")
        node = next((i for i in lcp_items if isinstance(i, dict) and i.get("type") == "node"), None)
        if node and node.get("selector"):
            data["lcp_selector"] = node["selector"]

    rows = _details(raw, "dom-size-insight").get("items", [])
    dom = {}
    for row in rows:
        field = _DOM_ROWS.get(row.get("statistic")) if isinstance(row, dict) else None
        value = (row.get("value") or {}).get("value") if field else None
        if field and isinstance(value, (int, float)):
            dom[field] = value
    if dom:
        entry(page_key).update(dom)
    return out
