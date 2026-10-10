"""Package clean Owner B network source deterministically; performs no AWS operations.

Run npm ci, then python scripts/package-owner-b-network.py [clean-source-checkout].
The optional checkout lets this helper reproduce an earlier pinned build revision.
"""
import hashlib
import json
import subprocess
import sys
import zipfile
from pathlib import Path


def main():
    root = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path(__file__).resolve().parents[1]
    def git(*args):
        return subprocess.check_output(['git', *args], cwd=root, text=True).strip()
    if git('status', '--porcelain'):
        raise RuntimeError('Build requires clean committed source')
    subprocess.check_call(['node', 'scripts/build-owner-b.cjs'], cwd=root)
    target = root / '.build/owner-b-network.zip'
    with zipfile.ZipFile(target, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name in ['network-upload.js', 'network.js']:
            entry = zipfile.ZipInfo(name, date_time=(2020, 1, 1, 0, 0, 0))
            entry.create_system = 0
            entry.external_attr = 0o644 << 16
            entry.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(entry, (root / '.build/owner-b' / name).read_bytes(), compresslevel=9)
    receipt = {'source_commit': git('rev-parse', 'HEAD'), 'source_tree': git('rev-parse', 'HEAD^{tree}'),
               'working_tree_uncommitted': False, 'archive_sha256': hashlib.sha256(target.read_bytes()).hexdigest(),
               'package_lock_sha256': hashlib.sha256((root / 'package-lock.json').read_bytes()).hexdigest(),
               'bytes': target.stat().st_size}
    (root / '.build/network-build.json').write_text(json.dumps(receipt, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(receipt))


if __name__ == '__main__':
    main()
