#!/usr/bin/env bash
# Read-only drift check for the owner-a stack: builds the Lambda zip from the current checkout and compares its
# CodeSha256 with the deployed owner-a-static-scan and owner-a-profile-parser functions (both share one zip).
# Exit 0 = deployed code equals this checkout; exit 1 = drift (redeploy needed); exit 2 = could not check.
#
#   AWS_PROFILE=shrey scripts/check-owner-a-deploy.sh
#   AWS_CLI=/path/to/aws AWS_REGION=ap-south-1 scripts/check-owner-a-deploy.sh   # when `aws` is not on PATH
#
# Uses only lambda:GetFunctionConfiguration; nothing is created, changed or deleted in AWS.
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
aws_cli="${AWS_CLI:-aws}"
region="${AWS_REGION:-ap-south-1}"
functions=(owner-a-static-scan owner-a-profile-parser)

bash "$root/scripts/build-owner-a-lambda.sh" >/dev/null
zip_path="$(ls "$root"/cdk/owner-a/build/owner-a-static-scan-*.zip | head -n 1)"
local_sha="$(python - "$zip_path" <<'PY'
import base64, hashlib, sys
print(base64.b64encode(hashlib.sha256(open(sys.argv[1], "rb").read()).digest()).decode())
PY
)"
echo "local build : $(basename "$zip_path")  CodeSha256=$local_sha"

drift=0
for fn in "${functions[@]}"; do
  if ! deployed="$("$aws_cli" lambda get-function-configuration --function-name "$fn" --region "$region" --query CodeSha256 --output text 2>&1)"; then
    echo "cannot read $fn: $deployed" >&2
    exit 2
  fi
  if [ "$deployed" = "$local_sha" ]; then
    echo "OK    $fn deployed CodeSha256 matches"
  else
    echo "DRIFT $fn deployed CodeSha256=$deployed"
    drift=1
  fi
done
if [ "$drift" -ne 0 ]; then
  echo "Deployed code differs from this checkout. Rebuild and redeploy (see detectors/owner-a/README.md)." >&2
  exit 1
fi
