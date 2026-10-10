#!/usr/bin/env bash
# Build the scan API Lambda artifacts (python3.12, arm64) into infra/scan-api/build/:
#   scan-api.zip  code for both Lambdas. The zip root mirrors the repository layout the scanner expects
#                 (REPO_ROOT = zip root):
#     scan_api/ scanner/ shared/contracts/ docs/taxonomy/checks.json
#     detectors/owner-c/owner_c/ detectors/owner-d/owner_d/  + manylinux aarch64 wheels of their deps
#     detectors/owner-a/{package.json,dist/,node_modules/}  compiled TypeScript + tree-sitter (linux-arm64 prebuilds)
#     detectors/owner-b/ (no tests) + node_modules/         the root production deps it loads (no @aws-sdk/*)
#   node-runtime-layer.zip  bin/node: the official Node.js linux-arm64 binary, sha256-pinned below. The worker
#                 gets it as a layer, so Lambda puts it at /opt/bin/node, which is on PATH.
# boto3 is not bundled: the Lambda runtime provides it. Builds locally only; uploads nothing.
# Handlers: scan_api.api.handler, scan_api.worker.handler. Needs python3, npm, curl and tar (xz).
set -euo pipefail
# From https://nodejs.org/dist/v22.23.3/SHASUMS256.txt
NODE_VERSION=v22.23.3
NODE_SHA256=a44aeb94849a299b22df10b9e622ec2f605c2183501bc40590705131de7c740f  # node-v22.23.3-linux-arm64.tar.xz
root="$(cd "$(dirname "$0")/.." && pwd)"
out="$root/infra/scan-api/build"
stage="$out/stage"
rm -rf "$out"
mkdir -p "$out/py" "$stage/a" "$stage/b" "$stage/node"
npm_linux=(--ignore-scripts --os=linux --cpu=arm64 --libc=glibc --no-audit --no-fund --loglevel=error)

python3 -m pip install --quiet --target "$out/py" --platform manylinux2014_aarch64 --platform manylinux_2_17_aarch64 \
  --python-version 3.12 --implementation cp --only-binary=:all: --no-warn-conflicts \
  -r "$root/shared/contracts/requirements.txt" -r "$root/detectors/owner-c/requirements.txt" \
  "typing_extensions>=4.4"  # referencing needs it on Python < 3.13; pip skips that marker when the build interpreter is newer

# Owner A: compile with the locked dev deps, then keep only the production deps for linux-arm64.
cp -R "$root/detectors/owner-a/package.json" "$root/detectors/owner-a/package-lock.json" \
  "$root/detectors/owner-a/tsconfig.json" "$root/detectors/owner-a/src" "$stage/a/"
(cd "$stage/a" && npm ci "${npm_linux[@]}" && npx tsc --outDir dist --declaration false --declarationMap false \
  --sourceMap false && rm -rf node_modules && npm ci --omit=dev "${npm_linux[@]}")
# Owner B: the root lockfile's production deps; the zip step keeps only those detectors/owner-b loads.
cp "$root/package.json" "$root/package-lock.json" "$stage/b/"
(cd "$stage/b" && npm ci --omit=dev "${npm_linux[@]}")

curl -fsSL -o "$stage/node.tar.xz" "https://nodejs.org/dist/$NODE_VERSION/node-$NODE_VERSION-linux-arm64.tar.xz"
python3 -c 'import hashlib, sys; d = hashlib.sha256(open(sys.argv[1], "rb").read()).hexdigest(); sys.exit(0 if d == sys.argv[2] else f"node tarball sha256 {d} != {sys.argv[2]}")' \
  "$stage/node.tar.xz" "$NODE_SHA256"
tar -xJf "$stage/node.tar.xz" -C "$stage/node" --strip-components=1 \
  "node-$NODE_VERSION-linux-arm64/bin/node" "node-$NODE_VERSION-linux-arm64/LICENSE"

python3 - "$root" "$out" <<'PY'
import hashlib
import json
import pathlib
import re
import sys
import zipfile

root, out = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
stage = out / "stage"
FIXED = (2020, 1, 1, 0, 0, 0)  # fixed timestamps and order: the same inputs give the same zip


def owner_b_packages(lock_path):
    """node_modules paths reachable from the root production deps, minus @aws-sdk/* (detectors/owner-b/index.js
    never loads them: only its Lambda handlers and collectors do)."""
    packages = json.loads(lock_path.read_text())["packages"]
    keep, todo = set(), [("", n) for n in packages[""].get("dependencies", {}) if not n.startswith("@aws-sdk/")]
    while todo:
        parent, name = todo.pop()
        base = parent
        while True:  # Node resolution: the nearest node_modules/<name>, walking up from the parent
            path = f"{base}/node_modules/{name}" if base else f"node_modules/{name}"
            if path in packages:
                break
            if not base:
                raise SystemExit(f"{name} (needed by {parent or 'package.json'}) is not in package-lock.json")
            base = base.rsplit("/node_modules/", 1)[0] if "/node_modules/" in base else ""
        if path not in keep and not packages[path].get("dev"):
            keep.add(path)
            todo += [(path, n) for n in packages[path].get("dependencies", {})]
    return keep


PACKAGE = re.compile(r"^((?:.*/)?node_modules/(?:@[^/]+/)?[^/]+)/")
OTHER_PREBUILDS = re.compile(r"/prebuilds/(?!linux-arm64/)")
skip_dirs = {"__pycache__", "tests", "test", "examples", ".bin"}


def wanted(rel):
    if skip_dirs & set(rel.parts) or any(p.endswith(".dist-info") for p in rel.parts):
        return False
    name = rel.name
    return not (name.endswith((".pyc", ".map", ".d.ts", ".d.mts", ".d.cts")) or OTHER_PREBUILDS.search(rel.as_posix()))


def write(z, path, arcname, mode=None):
    info = zipfile.ZipInfo(arcname, date_time=FIXED)
    info.external_attr = ((mode or (0o755 if path.stat().st_mode & 0o111 else 0o644)) | 0o100000) << 16
    info.compress_type = zipfile.ZIP_DEFLATED
    z.writestr(info, path.read_bytes())


def tree(base, prefix="", keep=None):
    for f in sorted(base.rglob("*")):
        rel = f.relative_to(base)
        if not f.is_file() or f.is_symlink() or not wanted(rel):
            continue
        if keep is not None:
            match = PACKAGE.match(rel.as_posix())
            if not match or match.group(1) not in keep:
                continue
        yield f, prefix + rel.as_posix()


b_keep = owner_b_packages(stage / "b" / "package-lock.json")
files = [(root / "docs/taxonomy/checks.json", "docs/taxonomy/checks.json")]
for d in ["scan_api", "scanner", "shared/contracts", "detectors/owner-c/owner_c", "detectors/owner-d/owner_d",
          "detectors/owner-b"]:
    files += tree(root / d, d + "/")
files += [f for f in tree(out / "py") if not f[1].startswith("bin/")]
files += [(stage / "a/package.json", "detectors/owner-a/package.json")]
files += tree(stage / "a/dist", "detectors/owner-a/dist/")
files += tree(stage / "a/node_modules", "detectors/owner-a/node_modules/")
files += tree(stage / "b", keep=b_keep)

zip_path, layer_path = out / "scan-api.zip", out / "node-runtime-layer.zip"
with zipfile.ZipFile(zip_path, "w") as z:
    for f, arcname in files:
        write(z, f, arcname)
with zipfile.ZipFile(layer_path, "w") as z:
    write(z, stage / "node/bin/node", "bin/node", 0o755)
    write(z, stage / "node/LICENSE", "share/doc/node/LICENSE", 0o644)

unzipped = {}
for path in (zip_path, layer_path):
    with zipfile.ZipFile(path) as z:
        unzipped[path] = sum(i.file_size for i in z.infolist())
    print(f"built {path.relative_to(root)}: {path.stat().st_size / 1e6:.1f} MB zipped, {unzipped[path] / 1e6:.1f} MB unzipped")
total = sum(unzipped.values())
print(f"worker code + layer unzipped: {total / 1e6:.1f} MB (Lambda limit 262.1 MB = 250 MiB)")
if total >= 250 * 2**20:
    sys.exit("over the 250 MiB unzipped Lambda limit")
for path, key in ((zip_path, "CodeKey"), (layer_path, "NodeLayerKey")):
    digest = hashlib.sha256(path.read_bytes()).hexdigest()[:16]
    print(f"suggested {key}: scan-api/{path.stem}-{digest}.zip")
PY
rm -rf "$out/py" "$stage"
