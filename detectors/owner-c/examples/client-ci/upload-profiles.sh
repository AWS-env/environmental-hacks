#!/usr/bin/env bash
# Example client-CI step for the owner C JavaScript checks (JS-02, 03, 04, 05, 07, 08).
#
#   upload-profiles.sh <repository_id> <commit_sha> <repo_dir> <entry.js>
#
# 1. zips the repository, 2. runs the entry point twice (CPU profile with --no-opt, heap profile with the shipped
# collector), 3. asks `owner-c-presign` for short-lived PUT URLs, 4. uploads the zip and the profiles, and 5. uploads
# manifest.json LAST: that upload triggers the parser Lambda. Needs: bash, python, node, curl and the AWS CLI with
# permission to invoke owner-c-presign. Presigned URLs are credentials: they are never printed.
#
# Only run an entry point you trust to run in your own CI; the detector never executes anything you upload.
set -euo pipefail

repo_id="${1:?repository_id, e.g. github:org/repo}"
sha="${2:?40-character commit sha}"
repo_dir="${3:?repository directory}"
entry="${4:?entry point relative to the repository directory}"
region="${AWS_REGION:-ap-south-1}"
collector="$(cd "$(dirname "$0")/../.." && pwd)/collectors/collect-heap.js"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

python - "$repo_dir" "$work/repo.zip" <<'PY'
import pathlib, sys, zipfile
src, out = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
skip = {".git", "node_modules"}
with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
    for f in sorted(src.rglob("*")):
        if f.is_file() and not skip & set(f.relative_to(src).parts):
            z.write(f, "repo/" + f.relative_to(src).as_posix())  # one top folder, like a GitHub archive: the parser drops it
PY

# V8 inlines small hot functions into their callers; --no-opt keeps function boundaries so the profile can be matched.
(cd "$repo_dir" && node --no-opt --cpu-prof --cpu-prof-dir="$work/cpu" "$entry" >/dev/null)
cp "$work"/cpu/*.cpuprofile "$work/cpuprofile.json"
# V8's default heap profile omits freed objects; the collector includes them.
(cd "$repo_dir" && HEAP_OUT="$work/heapprofile.json" node --no-opt -r "$collector" "$entry" >/dev/null)

aws lambda invoke --function-name owner-c-presign --region "$region" --cli-binary-format raw-in-base64-out \
  --payload "{\"repository_id\": \"$repo_id\", \"commit_sha\": \"$sha\", \"artifacts\": [\"cpuprofile\", \"heapprofile\"]}" \
  "$work/presign.json" >/dev/null
printf '{"repository_id": "%s", "commit_sha": "%s", "artifacts": ["cpuprofile", "heapprofile"]}' "$repo_id" "$sha" > "$work/manifest.json"

for name in repo cpuprofile heapprofile manifest; do  # manifest last
  file="$work/$name.json"; [ "$name" = repo ] && file="$work/repo.zip"
  url="$(python -c 'import json,sys; print(json.load(open(sys.argv[1]))["urls"][sys.argv[2]]["url"])' "$work/presign.json" "$name")"
  code="$(curl -sS -o /dev/null -w '%{http_code}' -X PUT --data-binary "@$file" "$url")"
  echo "uploaded $name: HTTP $code"
  [ "$code" = 200 ] || { echo "upload of $name failed" >&2; exit 1; }
done
echo "done: the parser Lambda runs from the manifest upload; results are published to the findings bus."
