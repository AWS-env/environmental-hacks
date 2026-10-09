#!/usr/bin/env bash
# Build the owner-c Lambda zips into cdk/owner-c/build/:
#   owner-c-detectors.zip  owner_c/ + tree-sitter wheels for Lambda (python3.13, arm64)
#   owner-c-xray-demo.zip  the traced demo workload with its npm dependency
# Handlers: owner_c.aws.handler / profile_handler / xray_handler / presign_handler .lambda_handler
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
out="$root/cdk/owner-c/build"
pkg="$root/detectors/owner-c"
rm -rf "$out"
mkdir -p "$out/py"
python -m pip install --quiet --target "$out/py" --platform manylinux2014_aarch64 --platform manylinux_2_17_aarch64 \
  --python-version 3.13 --implementation cp --only-binary=:all: -r "$pkg/requirements.txt"
python - "$pkg" "$out" <<'PY'
import pathlib
import sys
import zipfile

pkg, out = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
skip = {"__pycache__", "tests"}
with zipfile.ZipFile(out / "owner-c-detectors.zip", "w", zipfile.ZIP_DEFLATED) as z:
    for f in sorted((pkg / "owner_c").rglob("*")):
        if f.is_file() and not skip & set(f.parts) and f.suffix != ".pyc":
            z.write(f, f.relative_to(pkg).as_posix())
    for f in sorted((out / "py").rglob("*")):
        if f.is_file() and "__pycache__" not in f.parts and not f.name.endswith(".dist-info") and "dist-info" not in f.parts:
            z.write(f, f.relative_to(out / "py").as_posix())
print("built", out / "owner-c-detectors.zip")
PY
rm -rf "$out/py"
demo="$out/demo"
mkdir -p "$demo"
cp "$pkg/examples/xray-demo/index.js" "$pkg/examples/xray-demo/package.json" "$demo/"
(cd "$demo" && npm install --omit=dev --no-audit --no-fund --silent)
python - "$demo" "$out" <<'PY'
import pathlib
import sys
import zipfile

demo, out = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
with zipfile.ZipFile(out / "owner-c-xray-demo.zip", "w", zipfile.ZIP_DEFLATED) as z:
    for f in sorted(demo.rglob("*")):
        if f.is_file():
            z.write(f, f.relative_to(demo).as_posix())
print("built", out / "owner-c-xray-demo.zip")
PY
rm -rf "$demo"
