#!/usr/bin/env bash
# Build the owner-d telemetry analyzers zip into cdk/owner-d/build/:
#   owner-d-telemetry-<sha>.zip  owner_d/ (detectors + owner_d/aws handlers) + shared/contracts
#                                + docs/taxonomy/checks.json + jsonschema and its dependencies
# The analyzers run shared.contracts validate_pair before publishing, so jsonschema ships in the zip as
# wheels for the Lambda runtime (python3.12, arm64), installed like scripts/build-owner-d-hub.sh does.
# jsonschema's dependencies are pure Python except rpds-py (pulled in through referencing), which is a
# compiled manylinux aarch64 wheel. boto3 comes from the runtime. Handlers:
#   owner_d.aws.telemetry_handler / log_handler / trace_handler / artifact_handler .lambda_handler
# (artifact_handler is deployed by cdk/owner-d/artifact-parser.yaml from this same zip)
# The zip is reproducible (fixed timestamps, sorted entries) and named by its content hash so each change
# redeploys. Only this zip is replaced; the findings-hub build in the same folder is left alone.
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
out="$root/cdk/owner-d/build"
mkdir -p "$out"
deps="$(mktemp -d "${TMPDIR:-/tmp}/owner-d-telemetry-deps.XXXXXX")"
trap 'rm -rf "$deps"' EXIT
# pip evaluates environment markers with the interpreter running pip, not --python-version, so on a newer
# host it drops referencing's `typing-extensions; python_version < "3.13"`. Name it explicitly for 3.12.
python3 -m pip install --quiet --target "$deps" --platform manylinux2014_aarch64 --platform manylinux_2_17_aarch64 \
  --python-version 3.12 --implementation cp --only-binary=:all: -r "$root/shared/contracts/requirements.txt" \
  "typing-extensions>=4.4.0"
rm -f "$out"/owner-d-telemetry-*.zip
python3 - "$root" "$out" "$deps" <<'PY'
import hashlib
import pathlib
import sys
import zipfile

root, out, deps = (pathlib.Path(p) for p in sys.argv[1:4])
pkg = root / "detectors/owner-d"


def keep(path):
    return path.is_file() and "__pycache__" not in path.parts and not path.name.endswith((".pyc", ".pyo"))


files = [(f, f.relative_to(pkg)) for f in sorted((pkg / "owner_d").rglob("*.py")) if keep(f)]
files += [(f, f.relative_to(root)) for f in sorted((root / "shared/contracts").glob("*"))
          if f.suffix in (".py", ".json") and keep(f)]
files += [(root / "docs/taxonomy/checks.json", pathlib.Path("docs/taxonomy/checks.json"))]


def runtime_dep(rel):  # drop wheel metadata, console scripts and the packages' own tests/benchmarks
    return (rel.parts[0] != "bin" and not any(p.endswith(".dist-info") for p in rel.parts)
            and not set(rel.parts[1:-1]) & {"tests", "benchmarks"})


files += [(f, f.relative_to(deps)) for f in sorted(deps.rglob("*")) if keep(f) and runtime_dep(f.relative_to(deps))]
tmp = out / "telemetry.zip"
with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
    for src, arc in sorted(files, key=lambda item: item[1].as_posix()):
        info = zipfile.ZipInfo(arc.as_posix(), date_time=(2020, 1, 1, 0, 0, 0))
        info.external_attr = (0o755 if src.suffix == ".so" else 0o644) << 16
        z.writestr(info, src.read_bytes(), zipfile.ZIP_DEFLATED)
digest = hashlib.sha256(tmp.read_bytes()).hexdigest()[:16]
final = out / f"owner-d-telemetry-{digest}.zip"
tmp.rename(final)
print(final)
PY
