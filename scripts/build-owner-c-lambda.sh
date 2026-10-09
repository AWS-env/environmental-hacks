#!/usr/bin/env bash
# Build the owner-c Lambda zip: cdk/owner-c/build/owner-c-detectors.zip
# The zip root contains owner_c/ (no tests or caches), matching Handler
# owner_c.aws.handler.lambda_handler / owner_c.aws.profile_handler.lambda_handler.
# Lambda needs no extra dependencies: detectors use only the standard library and boto3 (runtime-provided).
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
out="$root/cdk/owner-c/build"
rm -rf "$out"
mkdir -p "$out"
python - "$root" "$out" <<'PY'
import pathlib
import sys
import zipfile

root, out = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
pkg = root / "detectors" / "owner-c"
target = out / "owner-c-detectors.zip"
with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as z:
    for f in sorted((pkg / "owner_c").rglob("*.py")):
        if "__pycache__" not in f.parts:
            z.write(f, f.relative_to(pkg).as_posix())
print("built", target)
PY
