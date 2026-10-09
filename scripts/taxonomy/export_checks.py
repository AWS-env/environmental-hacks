#!/usr/bin/env python3
"""
Export the Software Compute-Waste Taxonomy (v1.6 AWS mapping) from the source
.xlsx into repo-native files:

  docs/taxonomy/checks.yaml   one block per taxonomy row (taxonomy-as-code)
  docs/taxonomy/checks.json   machine mirror for the dependency-free sync script
  docs/taxonomy/mapping.json  key -> issue number/node_id/content_hash (seed)
  docs/taxonomy/owners.json   owner <-> handle mapping (single editable column)

Requirements:  pip install openpyxl pyyaml
Usage:         python scripts/taxonomy/export_checks.py <path-to-xlsx>

The source workbook is treated as external/authoritative. Re-running is safe;
existing mapping.json is preserved (only keys that are missing get added).
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from datetime import datetime, timezone

import yaml

try:
    import openpyxl
except ImportError:  # pragma: no cover
    sys.exit("Missing dependency: pip install openpyxl pyyaml")

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
OUT_DIR = os.path.join(REPO_ROOT, "docs", "taxonomy")
CHECK_PATH = os.path.join(OUT_DIR, "checks.yaml")
CHECK_JSON = os.path.join(OUT_DIR, "checks.json")
MAPPING_PATH = os.path.join(OUT_DIR, "mapping.json")
OWNERS_PATH = os.path.join(OUT_DIR, "owners.json")

# MASTER Taxonomy column indices
MASTER = {
    "id": 0, "owner": 1, "layer": 2, "category": 3, "pattern": 4,
    "wasteful_behavior": 5, "not_wasteful_when": 6, "affected_resource": 7,
    "detection_method": 8, "candidate_optimization": 9, "applicability": 10,
    "source": 11, "evidence_type": 12, "confidence": 13, "notes": 14,
    "status_v13": 15, "aws_detector": 16, "aws_run_location": 17,
    "needs_iam_role": 18, "needs_discussion": 19, "mapping_confidence": 20,
    "assessment_source": 21,
}
# AWS Mapping column indices
AWS = {"id": 0, "rule": 10, "rationale": 11}

# Coarse layer -> label slug
LAYER_SLUG = {
    "Code (algo/CPU/mem/GC)": "code",
    "Code (runtime/backend)": "code",
    "Code (Python)": "code",
    "Code (JS/TS)": "code",
    "Database access": "database",
    "Network / API / serialization": "network",
    "Background jobs / scheduling": "jobs",
    "Frontend (client)": "frontend",
    "CI/CD & build": "ci",
    "Test code": "test",
    "Observability": "observability",
    "LLM / agentic calls": "llm",
    "Infrastructure / capacity": "infrastructure",
}

EPICS = {
    "A": "[Owner A] In-process code efficiency",
    "B": "[Owner B] Data & external I/O",
    "C": "[Owner C] Language, client & build",
    "D": "[Owner D] Runtime ops & AI",
}


def clean(v) -> str:
    if v is None:
        return ""
    return str(v).replace("\r\n", "\n").strip()


def content_hash(row: dict) -> str:
    basis = "|".join(
        str(row.get(k, ""))
        for k in ("key", "pattern", "detection_method", "rule", "aws_detector")
    )
    return hashlib.sha1(basis.encode("utf-8")).hexdigest()[:8]


def main(xlsx_path: str) -> None:
    wb = openpyxl.load_workbook(xlsx_path, data_only=True)
    master = list(wb["MASTER Taxonomy"].iter_rows(values_only=True))
    aws = list(wb["AWS Mapping (v1.6)"].iter_rows(values_only=True))

    aws_by_id = {clean(r[AWS["id"]]): r for r in aws[1:] if clean(r[AWS["id"]])}

    checks = []
    for r in master[1:]:
        key = clean(r[MASTER["id"]])
        if not key:
            continue
        arow = aws_by_id.get(key, ())
        layer = clean(r[MASTER["layer"]])
        owner = clean(r[MASTER["owner"]])
        rule = clean(arow[AWS["rule"]]) if arow else ""
        rec = {
            "key": key,
            "owner": owner,
            "layer": layer,
            "layer_label": LAYER_SLUG.get(layer, layer.lower()),
            "category": clean(r[MASTER["category"]]),
            "pattern": clean(r[MASTER["pattern"]]),
            "wasteful_behavior": clean(r[MASTER["wasteful_behavior"]]),
            "not_wasteful_when": clean(r[MASTER["not_wasteful_when"]]),
            "affected_resource": clean(r[MASTER["affected_resource"]]),
            "detection_method": clean(r[MASTER["detection_method"]]),
            "candidate_optimization": clean(r[MASTER["candidate_optimization"]]),
            "applicability": clean(r[MASTER["applicability"]]),
            "aws_detector": clean(r[MASTER["aws_detector"]]),
            "aws_run_location": clean(r[MASTER["aws_run_location"]]),
            "rule": rule,
            "needs_iam_role": clean(r[MASTER["needs_iam_role"]]),
            "needs_discussion": clean(r[MASTER["needs_discussion"]]),
            "mapping_confidence": clean(r[MASTER["mapping_confidence"]]),
            "rationale": clean(arow[AWS["rationale"]]) if arow else "",
            "source": clean(r[MASTER["source"]]),
            "evidence_type": clean(r[MASTER["evidence_type"]]),
            "confidence": clean(r[MASTER["confidence"]]),
            "notes": clean(r[MASTER["notes"]]),
            "status_v13": clean(r[MASTER["status_v13"]]),
            # detection-spec fields: filled by each owner (TBD in the source)
            "detection_signal": "",
            "detection_tool": "",
            "telemetry_needed": "",
            "report_field": "",
            "false_positive_risk": "",
            "detectable": "",
            "measurable": "",
            "verify_status": "Unverified",
        }
        rec["content_hash"] = content_hash(rec)
        checks.append(rec)

    checks.sort(key=lambda c: c["key"])
    os.makedirs(OUT_DIR, exist_ok=True)

    with open(CHECK_PATH, "w", encoding="utf-8") as fh:
        fh.write("# Software Compute-Waste Taxonomy - one entry per check.\n")
        fh.write("# Generated by scripts/taxonomy/export_checks.py - edit the xlsx, not this file.\n")
        fh.write(f"# Rows: {len(checks)}\n\n")
        yaml.safe_dump(checks, fh, sort_keys=False, allow_unicode=True, width=10000)

    # checks.json: machine-readable mirror consumed by scripts/sync-taxonomy-issues.ts
    # (keeps the sync script dependency-free; checks.yaml stays the human source)
    with open(CHECK_JSON, "w", encoding="utf-8") as fh:
        json.dump(checks, fh, indent=2)
        fh.write("\n")

    # mapping.json: preserve existing issue numbers; add/refresh hashes
    existing = {}
    if os.path.exists(MAPPING_PATH):
        with open(MAPPING_PATH, encoding="utf-8") as fh:
            existing = json.load(fh)
    prev_checks = existing.get("checks", {})
    prev_epics = existing.get("epics", {})

    mapping = {
        "repo": "AWS-env/environmental-hacks",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "epics": {
            k: prev_epics.get(k, {"number": None, "node_id": None, "title": t, "hash": None})
            for k, t in EPICS.items()
        },
        "checks": {},
    }
    for c in checks:
        prev = prev_checks.get(c["key"], {})
        mapping["checks"][c["key"]] = {
            "number": prev.get("number"),
            "node_id": prev.get("node_id"),
            "title": f'[{c["key"]}] {c["pattern"]}',
            "owner": c["owner"],
            "hash": c["content_hash"],
        }
    with open(MAPPING_PATH, "w", encoding="utf-8") as fh:
        json.dump(mapping, fh, indent=2)
        fh.write("\n")

    if not os.path.exists(OWNERS_PATH):
        owners = {
            "_comment": "PLACEHOLDER: change ONLY the `handle` values once owners are decided.",
            "repo": "AWS-env/environmental-hacks",
            "team": "AWS-env/environmental-hacks",
            "owners": {
                "A": {"label": "owner:A", "handle": "MrPanda009"},
                "B": {"label": "owner:B", "handle": "shryssssss-maker"},
                "C": {"label": "owner:C", "handle": "Medhansh-741"},
                "D": {"label": "owner:D", "handle": "prx-my"},
            },
        }
        with open(OWNERS_PATH, "w", encoding="utf-8") as fh:
            json.dump(owners, fh, indent=2)
            fh.write("\n")

    print(f"wrote {CHECK_PATH} ({len(checks)} checks)")
    print(f"wrote {CHECK_JSON}")
    print(f"wrote {MAPPING_PATH}")
    print(f"owners config: {OWNERS_PATH}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(sys.argv[1])
