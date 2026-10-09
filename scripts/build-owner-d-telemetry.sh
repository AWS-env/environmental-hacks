#!/usr/bin/env bash
# Build the owner-d telemetry analyzers zip into cdk/owner-d/build/:
#   owner-d-telemetry-<sha>.zip  owner_d/ (detectors + owner_d/aws handlers), pure Python, no dependencies
# boto3 comes from the Lambda runtime (python3.12, arm64). Handlers:
#   owner_d.aws.telemetry_handler / log_handler / trace_handler .lambda_handler
# The zip is reproducible (fixed timestamps, sorted entries) and named by its content hash so each change
# redeploys. Only this zip is replaced; the findings-hub build in the same folder is left alone.
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
out="$root/cdk/owner-d/build"
mkdir -p "$out"
rm -f "$out"/owner-d-telemetry-*.zip
python3 - "$root/detectors/owner-d" "$out" <<'PY'
import hashlib
import pathlib
import sys
import zipfile

pkg, out = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
files = sorted(f for f in (pkg / "owner_d").rglob("*.py") if "__pycache__" not in f.parts)
tmp = out / "telemetry.zip"
with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
    for src in files:
        info = zipfile.ZipInfo(src.relative_to(pkg).as_posix(), date_time=(2020, 1, 1, 0, 0, 0))
        info.external_attr = 0o644 << 16
        z.writestr(info, src.read_bytes(), zipfile.ZIP_DEFLATED)
digest = hashlib.sha256(tmp.read_bytes()).hexdigest()[:16]
final = out / f"owner-d-telemetry-{digest}.zip"
tmp.rename(final)
print(final)
PY
