# Taxonomy → GitHub issues

How the Software Compute-Waste Taxonomy becomes one GitHub issue per check, so each
owner can work their own checks without overlap.

## Files

| File | Role |
| --- | --- |
| `checks.yaml` | Human source of truth. One entry per check. **Generated from the xlsx — edit the xlsx, not this file.** |
| `checks.json` | Machine mirror of `checks.yaml`, read by the sync script (keeps it dependency-free). |
| `owners.json` | **The single place to change owners.** `handle` is the GitHub username; `aws_user` is the AWS IAM user name. |
| `mapping.json` | Links each `KEY` to its GitHub issue (`number`, `node_id`, `hash`). Committed so re-runs are idempotent. |
| `../ARCHITECTURE_FLOWS.md` | The user/our/AWS flow diagrams. |

## Identifiers

- **Key** = the taxonomy ID (`CODE-C1.1`, `DB-01`, …). Stable, never changes.
- **Issue title** = `[KEY] <pattern>` — searchable by key.
- **content_hash** = `sha1(key|pattern|detection_method|rule|aws_detector)[:8]`; the sync script
  only re-writes an issue when this changes. This is the "hash per check".

## Owners

| Label | GitHub | AWS user |
| --- | --- | --- |
| `owner:A` | [@mrpanda](https://github.com/mrpanda) | `shrey` |
| `owner:B` | [@shreyas](https://github.com/shreyas) | `shreyas` |
| `owner:C` | [@medhansh](https://github.com/medhansh) | `medhansh` |
| `owner:D` | [@prXmy](https://github.com/prXmy) | `prakhar` |

## Regenerate from the source workbook

```bash
pip install openpyxl pyyaml
python scripts/taxonomy/export_checks.py path/to/Software_Compute_Waste_Taxonomy_v1.6_aws_mapping.xlsx
```

Re-running preserves existing issue numbers in `mapping.json` and only refreshes hashes.

## Create / update the GitHub issues

Needs a token with **issues:write** (classic PAT: `repo`; fine-grained: Issues RW + Metadata R).

```bash
export GITHUB_TOKEN=...                 # or GH_TOKEN

# 1) dry-run everything first
node scripts/taxonomy-sync.mts setup
node scripts/taxonomy-sync.mts sync

# 2) apply
node scripts/taxonomy-sync.mts setup --apply   # labels + .github/CODEOWNERS
node scripts/taxonomy-sync.mts sync  --apply   # 4 owner epics + 251 checks + sub-issues
```

- `--limit N` and `--only KEY` let you test on a few checks first.
- Writes are paced (~1.1s) and checkpoint `mapping.json` every 25 issues.
- `setup` writes `CODEOWNERS` locally; commit it via a PR (main is protected).

Optional: run the same via **Actions → Taxonomy sync** (`workflow_dispatch`). It can create
labels/issues using the workflow token; it cannot commit to protected `main`, so `mapping.json`
is uploaded as a workflow artifact for you to commit.

## Working a check

1. Pick an issue with your `owner:*` label.
2. Branch `owner-x/<issue>-feat-<key>-<slug>` (e.g. `owner-a/123-feat-code-c1-1-detector`).
3. Commit `#<issue> | feat(owner-x): <summary>`.
4. PR `Closes #<issue>` → CODEOWNERS review → squash-merge.
