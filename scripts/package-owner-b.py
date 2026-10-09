"""Package the four prebuilt Owner B bundles with reproducible ZIP metadata."""
from pathlib import Path
import hashlib
import json
import subprocess
import zipfile


def main():
    root = Path(__file__).resolve().parents[1]
    def git(*args):
        return subprocess.run(["git", *args], cwd=root, text=True,
                              capture_output=True, check=True).stdout.strip()
    if git("status", "--porcelain"):
        raise RuntimeError("Build source must be committed and clean")
    bundles = sorted((root / ".build/owner-b").glob("*.js"))
    if {p.name for p in bundles} != {"static.js", "log.js", "telemetry.js", "heuristic.js"}:
        raise RuntimeError("Run npm run build:owner-b before packaging")
    archive = root / ".build/jobs.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as output:
        for bundle in bundles:
            info = zipfile.ZipInfo(bundle.name, date_time=(2020, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            output.writestr(info, bundle.read_bytes(), compresslevel=9)
    digest = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
    print(json.dumps({"build_commit": git("rev-parse", "HEAD"), "working_tree_clean": True,
                      "lock_sha256": digest(root / "package-lock.json"),
                      "archive_sha256": digest(archive),
                      "bundle_sha256": {p.name: digest(p) for p in bundles}}, indent=2))


if __name__ == "__main__":
    main()
