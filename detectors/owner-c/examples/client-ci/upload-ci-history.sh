#!/usr/bin/env bash
# Example client-CI step for the owner C CI checks (CI-01, 02, 03, 05 and, with the repo zip, CI-12 and CI-18).
#
#   upload-ci-history.sh <repository_id> <commit_sha> <repo_dir> <workflow.yml> [<workflow.yml> ...]
#
# 1. collects the GitHub Actions run history of the named workflows with the single-file collector (the job's own
#    GITHUB_TOKEN with `permissions: actions: read` is enough), 2. zips the repository, 3. asks `owner-c-presign` for
#    short-lived PUT URLs, 4. uploads repo.zip and ci_history.json, and 5. uploads manifest.json LAST: that upload
#    triggers the parser Lambda. Needs: bash, python, curl and the AWS CLI with permission to invoke owner-c-presign.
#    Presigned URLs are credentials: they are never printed. Nothing you upload is ever executed.
#
# For tests, CI_HISTORY_JSON=<file> uses an existing bundle instead of calling the GitHub API.
set -euo pipefail

repo_id="${1:?repository_id, e.g. github:org/repo}"
sha="${2:?40-character commit sha}"
repo_dir="${3:?repository directory}"
shift 3
[ "$#" -ge 1 ] || { echo "name at least one workflow file, e.g. ci.yml" >&2; exit 2; }
region="${AWS_REGION:-ap-south-1}"
collector="${COLLECTOR:-$(cd "$(dirname "$0")/../.." && pwd)/owner_c/ci/collector.py}"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

if [ -n "${CI_HISTORY_JSON:-}" ]; then
  cp "$CI_HISTORY_JSON" "$work/ci_history.json"
else
  args=()
  for workflow in "$@"; do args+=(--workflow "$workflow"); done
  python "$collector" --repo "${repo_id#github:}" "${args[@]}" > "$work/ci_history.json"
fi

python - "$repo_dir" "$work/repo.zip" <<'PY'
import pathlib, sys, zipfile
src, out = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
skip = {".git", "node_modules"}
with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
    for f in sorted(src.rglob("*")):
        if f.is_file() and not skip & set(f.relative_to(src).parts):
            z.write(f, "repo/" + f.relative_to(src).as_posix())  # one top folder, like a GitHub archive: the parser drops it
PY

aws lambda invoke --function-name owner-c-presign --region "$region" --cli-binary-format raw-in-base64-out \
  --payload "{\"repository_id\": \"$repo_id\", \"commit_sha\": \"$sha\", \"artifacts\": [\"ci_history\"]}" \
  "$work/presign.json" >/dev/null
printf '{"repository_id": "%s", "commit_sha": "%s", "artifacts": ["ci_history"]}' "$repo_id" "$sha" > "$work/manifest.json"

for name in repo ci_history manifest; do  # manifest last
  file="$work/$name.json"; [ "$name" = repo ] && file="$work/repo.zip"
  url="$(python -c 'import json,sys; print(json.load(open(sys.argv[1]))["urls"][sys.argv[2]]["url"])' "$work/presign.json" "$name")"
  code="$(curl -sS -o /dev/null -w '%{http_code}' -X PUT --data-binary "@$file" "$url")"
  echo "uploaded $name: HTTP $code"
  [ "$code" = 200 ] || { echo "upload of $name failed" >&2; exit 1; }
done
echo "done: the parser Lambda runs from the manifest upload; results are published to the findings bus."
