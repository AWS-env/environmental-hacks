"""Test helpers: build contract inputs, evaluate, and validate with the shared contract."""
import json
import pathlib

from owner_c.connector import build_inputs
from owner_c.runner import evaluate
from shared.contracts.validation import validate_pair

FIXTURES = pathlib.Path(__file__).parent / "fixtures"
REPO = "github:example/app"
SHA = "a" * 40
SCAN = "scan-1"


def run_check(check_id, files, artifacts=None, context=None, include_tests=False):
    """Evaluate one check over {path: source}; returns (input_payload, result_payload) after contract validation."""
    payloads = build_inputs(repository_id=REPO, commit_sha=SHA, scan_id=SCAN, files=list(files.items()),
                            artifacts=artifacts, include_tests=include_tests, checks=[check_id])
    assert len(payloads) == 1, f"no input built for {check_id}"
    payload = payloads[0]
    if context:
        payload["context"] = {**payload["context"], **context}
    result = evaluate(payload)
    validate_pair(payload, result)  # shape, fingerprints, coverage AND evidence quotes against the input
    return payload, result


def load_json(path):
    return json.loads(pathlib.Path(path).read_text())
