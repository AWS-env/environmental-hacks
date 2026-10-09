"""Test helpers for the CI detectors: build contract inputs, evaluate, validate with the shared contract."""
import pathlib

from owner_c.ci.connector import build_inputs
from owner_c.ci.runner import evaluate
from shared.contracts.validation import validate_pair

FIXTURES = pathlib.Path(__file__).parent / "fixtures"
REPO = "github:example/app"
SHA = "a" * 40
SCAN = "scan-1"


def run_ci_check(check_id, files, artifacts=None, context=None):
    """Evaluate one CI check over {path: text}; returns (input, result) after contract validation."""
    payloads = build_inputs(repository_id=REPO, commit_sha=SHA, scan_id=SCAN, files=list(files.items()),
                            artifacts=artifacts, checks=[check_id])
    assert len(payloads) == 1, f"no input built for {check_id}"
    payload = payloads[0]
    if context:
        payload["context"] = {**payload["context"], **context}
    result = evaluate(payload)
    validate_pair(payload, result)  # shape, fingerprints, coverage AND evidence quotes against the input
    return payload, result
