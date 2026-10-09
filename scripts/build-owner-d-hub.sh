#!/usr/bin/env bash
# Build the owner-d findings-hub writer zip into cdk/owner-d/build/:
#   owner-d-findings-hub-<sha>.zip  findings_hub/ + shared/contracts + docs/taxonomy/checks.json
#                                   + jsonschema wheels for Lambda (python3.13, arm64)
# Handler: findings_hub.writer.lambda_handler. The content hash in the name makes each change redeploy.
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
out="$root/cdk/owner-d/build"
rm -rf "$out"
mkdir -p "$out/py"
python3 -m pip install --quiet --target "$out/py" --platform manylinux2014_aarch64 --platform manylinux_2_17_aarch64 \
  --python-version 3.13 --implementation cp --only-binary=:all: -r "$root/shared/contracts/requirements.txt"
python3 - "$root" "$out" <<'PY'
import hashlib
import pathlib
import sys
import zipfile

root, out = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
files = [(f, f.relative_to(root / "hub")) for f in sorted((root / "hub/findings_hub").rglob("*.py"))]
files += [(root / "shared" / n, pathlib.Path("shared") / n) for n in ("__init__.py",) if (root / "shared" / n).exists()]
files += [(f, f.relative_to(root)) for f in sorted((root / "shared/contracts").glob("*")) if f.suffix in (".py", ".json")]
files += [(root / "docs/taxonomy/checks.json", pathlib.Path("docs/taxonomy/checks.json"))]
files += [(f, f.relative_to(out / "py")) for f in sorted((out / "py").rglob("*"))
          if f.is_file() and "__pycache__" not in f.parts and not any(p.endswith(".dist-info") for p in f.parts)]
tmp = out / "writer.zip"
with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
    for src, arc in files:
        info = zipfile.ZipInfo(arc.as_posix(), date_time=(2020, 1, 1, 0, 0, 0))
        info.external_attr = 0o644 << 16
        z.writestr(info, src.read_bytes(), zipfile.ZIP_DEFLATED)
digest = hashlib.sha256(tmp.read_bytes()).hexdigest()[:16]
final = out / f"owner-d-findings-hub-{digest}.zip"
tmp.rename(final)
print(final)
PY
rm -rf "$out/py"
