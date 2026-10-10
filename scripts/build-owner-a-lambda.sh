#!/usr/bin/env bash
# Build the owner-a static-scan Lambda zip into cdk/owner-a/build/:
#   owner-a-static-scan-<sha>.zip   compiled dist/ + production node_modules for linux-arm64 (nodejs22.x)
# Handler: dist/aws/handler.handler (ESM). The content hash in the name makes each change redeploy.
# @aws-sdk/* is provided by the Lambda runtime, so only tree-sitter and its Python grammar are bundled.
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
pkg="$root/detectors/owner-a"
out="$root/cdk/owner-a/build"
stage="$out/stage"
rm -rf "$out"
mkdir -p "$stage"
(cd "$pkg" && npx tsc --outDir "$stage/dist" --declaration false --declarationMap false --sourceMap false)
cp "$pkg/package.json" "$pkg/package-lock.json" "$stage/"
(cd "$stage" && npm ci --omit=dev --ignore-scripts --os=linux --cpu=arm64 --libc=glibc --no-audit --no-fund --loglevel=error)
python - "$stage" "$out" <<'PY'
import hashlib
import pathlib
import sys
import zipfile

stage, out = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
files = [f for f in sorted(stage.rglob("*")) if f.is_file() and not f.name.endswith(".map")]
tmp = out / "static-scan.zip"
with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
    for f in files:
        rel = f.relative_to(stage)
        info = zipfile.ZipInfo(rel.as_posix(), date_time=(2020, 1, 1, 0, 0, 0))
        mode = 0o755 if f.suffix == ".node" else 0o644
        info.external_attr = mode << 16
        z.writestr(info, f.read_bytes(), zipfile.ZIP_DEFLATED)
digest = hashlib.sha256(tmp.read_bytes()).hexdigest()[:16]
final = out / f"owner-a-static-scan-{digest}.zip"
tmp.rename(final)
print(final, round(final.stat().st_size / 1e6, 2), "MB zipped")
PY
rm -rf "$stage"
