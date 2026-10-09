#!/usr/bin/env bash
# Build infra/scan-api/build/scan-api.zip for both scan API Lambdas (python3.12, arm64).
# The zip root mirrors the repository layout the scanner expects (REPO_ROOT = zip root):
#   scan_api/ scanner/ shared/contracts/ docs/taxonomy/checks.json
#   detectors/owner-c/owner_c/ detectors/owner-d/owner_d/  + manylinux aarch64 wheels of their deps
# boto3 is not bundled: the Lambda runtime provides it. Owners A and B need Node.js and are not bundled.
# Handlers: scan_api.api.handler, scan_api.worker.handler. Builds locally only; uploads nothing.
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
out="$root/infra/scan-api/build"
rm -rf "$out"
mkdir -p "$out/py"
python3 -m pip install --quiet --target "$out/py" --platform manylinux2014_aarch64 --platform manylinux_2_17_aarch64 \
  --python-version 3.12 --implementation cp --only-binary=:all: --no-warn-conflicts \
  -r "$root/shared/contracts/requirements.txt" -r "$root/detectors/owner-c/requirements.txt"
python3 - "$root" "$out" <<'PY'
import hashlib
import pathlib
import sys
import zipfile

root, out = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
sources = [
    (root, ["scan_api", "scanner", "shared/contracts", "detectors/owner-c/owner_c", "detectors/owner-d/owner_d"]),
    (out / "py", ["."]),
]
skip_dirs = {"__pycache__", "tests", "examples"}
zip_path = out / "scan-api.zip"
with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
    z.write(root / "docs/taxonomy/checks.json", "docs/taxonomy/checks.json")
    for base, dirs in sources:
        for d in dirs:
            for f in sorted((base / d).rglob("*")):
                rel = f.relative_to(base)
                if (f.is_file() and not skip_dirs & set(rel.parts) and f.suffix not in (".pyc", ".mjs")
                        and not any(p.endswith(".dist-info") for p in rel.parts) and rel.parts[0] != "bin"):
                    z.write(f, rel.as_posix())
digest = hashlib.sha256(zip_path.read_bytes()).hexdigest()[:16]
print(f"built {zip_path} ({zip_path.stat().st_size // 1024} KiB)")
print(f"suggested CodeKey: scan-api/scan-api-{digest}.zip")
PY
rm -rf "$out/py"
