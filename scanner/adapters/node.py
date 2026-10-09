"""Run `scanner/node_driver.mjs` for the Node-based detectors: one JSON request, one JSON response."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from scanner.core import AdapterUnavailable

DRIVER = Path(__file__).resolve().parents[1] / "node_driver.mjs"
TIMEOUT_SECONDS = 600


def call(owner: str, entry: Path, action: str, request=None, timeout=TIMEOUT_SECONDS):
    node = shutil.which("node")
    if node is None:
        raise AdapterUnavailable("Node.js (>= 22) is not on PATH")
    if not entry.exists():
        raise AdapterUnavailable(f"{entry.name} not found at {entry}")
    try:
        proc = subprocess.run([node, str(DRIVER), owner, str(entry), action],
                              input=json.dumps(request) if request is not None else "",
                              capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"Node detector timed out after {timeout}s") from None
    if proc.returncode != 0:
        raise RuntimeError(f"Node detector exited {proc.returncode}: {proc.stderr.strip()[-400:]}")
    response = json.loads(proc.stdout)
    if "unavailable" in response:
        raise AdapterUnavailable(response["unavailable"])
    return response
