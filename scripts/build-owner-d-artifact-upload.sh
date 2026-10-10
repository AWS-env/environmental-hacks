#!/usr/bin/env bash
# Build the owner-d artifact upload zip into cdk/owner-d/build/:
#   owner-d-artifact-upload-<sha>.zip  artifact_upload/ + PyJWT[crypto] wheels for Lambda (python3.12, arm64)
# PyJWT verifies the GitHub Actions OIDC token; its crypto extra pulls in cryptography (compiled abi3 manylinux
# aarch64 wheel) and cffi. boto3 comes from the runtime. Handler: artifact_upload.api.handler.
# The zip is reproducible (fixed timestamps, sorted entries) and named by its content hash so each change
# redeploys. Only this zip is replaced; the other owner-d builds in the same folder are left alone.
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
out="$root/cdk/owner-d/build"
mkdir -p "$out"
deps="$(mktemp -d "${TMPDIR:-/tmp}/owner-d-artifact-upload-deps.XXXXXX")"
trap 'rm -rf "$deps"' EXIT
python3 -m pip install --quiet --target "$deps" --platform manylinux2014_aarch64 --platform manylinux_2_17_aarch64 \
  --platform manylinux_2_28_aarch64 --platform manylinux_2_34_aarch64 \
  --python-version 3.12 --implementation cp --only-binary=:all: -r "$root/hub/artifact_upload/requirements.txt"
rm -f "$out"/owner-d-artifact-upload-*.zip
python3 - "$root" "$out" "$deps" <<'PY'
import hashlib
import pathlib
import sys
import zipfile

root, out, deps = (pathlib.Path(p) for p in sys.argv[1:4])


def keep(path):
    return path.is_file() and "__pycache__" not in path.parts and not path.name.endswith((".pyc", ".pyo"))


def runtime_dep(rel):  # drop wheel metadata and console scripts
    return rel.parts[0] != "bin" and not any(p.endswith(".dist-info") for p in rel.parts)


files = [(f, f.relative_to(root / "hub")) for f in sorted((root / "hub/artifact_upload").rglob("*.py")) if keep(f)]
files += [(f, f.relative_to(deps)) for f in sorted(deps.rglob("*")) if keep(f) and runtime_dep(f.relative_to(deps))]
tmp = out / "artifact-upload.zip"
with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
    for src, arc in sorted(files, key=lambda item: item[1].as_posix()):
        info = zipfile.ZipInfo(arc.as_posix(), date_time=(2020, 1, 1, 0, 0, 0))
        info.external_attr = (0o755 if src.suffix == ".so" else 0o644) << 16
        z.writestr(info, src.read_bytes(), zipfile.ZIP_DEFLATED)
digest = hashlib.sha256(tmp.read_bytes()).hexdigest()[:16]
final = out / f"owner-d-artifact-upload-{digest}.zip"
tmp.rename(final)
print(final)
PY
