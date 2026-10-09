#!/usr/bin/env bash
# Build the owner-d telemetry demo zip into cdk/owner-d/build/:
#   owner-d-telemetry-demo-<sha>.zip  handler.py only (no dependencies: boto3 ships with the python3.12 runtime,
#                                     X-Ray subsegments go to the Lambda daemon over UDP)
# Handler: handler.lambda_handler. The content hash in the name makes each change redeploy.
# Only replaces earlier telemetry-demo zips; build-owner-d-hub.sh wipes the whole folder, so upload right after.
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
out="$root/cdk/owner-d/build"
mkdir -p "$out"
rm -f "$out"/owner-d-telemetry-demo-*.zip
python3 - "$root/detectors/owner-d/examples/telemetry-demo/handler.py" "$out" <<'PY'
import hashlib
import pathlib
import sys
import zipfile

src, out = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
tmp = out / "telemetry-demo.zip"
with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
    info = zipfile.ZipInfo("handler.py", date_time=(2020, 1, 1, 0, 0, 0))
    info.external_attr = 0o644 << 16
    z.writestr(info, src.read_bytes(), zipfile.ZIP_DEFLATED)
digest = hashlib.sha256(tmp.read_bytes()).hexdigest()[:16]
final = out / f"owner-d-telemetry-demo-{digest}.zip"
tmp.rename(final)
print(final)
PY
