# Client-side evidence collectors for the 10 artifact detectors without a client uploader

Status: implemented in issue #495 (action + tests); real end-to-end upload verified by the owner-c-client-action-test workflow. Audit date 2026-10-10, against origin/main.

## Problem

Ten merged detectors need evidence from running the client's code. The server side (normalizer, parser Lambda,
`owner-c-presign`) exists, but there is no client-side script that produces and uploads the artifact.

| Detectors | PRs | Artifact type | Tool the client runs |
| --- | --- | --- | --- |
| PY-01 | #300 | `speedscope` | py-spy |
| PY-05, PY-11 | #301, #302 | `memray_stats` | memray |
| FE-02, 10, 11, 13, 15, 16 | #366-371 | `lighthouse` | Lighthouse 13 |
| CODE-C6.1 (owner-a) | #416 | memray stats | memray (separate track, see Open decisions) |

Already complete, no work needed: JS-02/03/04/05/07/08 (`upload-profiles.sh`), CI-01/02/03/05/12/18
(`upload-ci-history.sh`), JS-01 (X-Ray read from the client's account).

## Constraints

- We never execute uploaded or client code. The client's own runner runs the tool; we only parse.
- One click for the client: they enable it once, then it runs on every push. No manual steps.
- Presigned URLs are credentials and are never printed. Upload order is repo.zip, artifacts, manifest last.
- If a tool cannot run, upload nothing: the result is `unavailable`, never clean.

## Delivery mechanism

| Option | Client effort | Verdict |
| --- | --- | --- |
| Hand-written CI step | copy 30 lines, maintain them | rejected: not one click |
| `.sh` script | download, wire into CI, install deps | keep as the engine, not the interface |
| Reusable composite GitHub Action | one `uses:` line | chosen |
| GitHub App that opens a PR adding the workflow | one click to install | later layer on top of the action |

AWS access from the action: GitHub OIDC with `aws-actions/configure-aws-credentials` and a role that can only
invoke `owner-c-presign`, so the client stores no secrets. The workflow needs `id-token: write`; the role trust
policy should be scoped by the `sub` claim to the client's repository.

## Tool facts

Verified by web search (see Sources):

- py-spy: `py-spy record --format speedscope -o profile.json -- python app.py`. Launching the process under py-spy
  needs no root. Attaching to a running PID needs sudo or a relaxed `kernel.yama.ptrace_scope`. In Docker the
  default seccomp profile blocks it; the container needs `--cap-add SYS_PTRACE`.
- memray: `memray stats --json` writes the stats as JSON and takes `-o`. Totals, size histogram and allocator
  distribution are included.
- AWS OIDC: `id-token: write` plus `role-to-assume`.

NOT yet verified, check before implementing:

- py-spy `--nonblocking` behaviour: avoid it unless confirmed with `py-spy record --help`.
- memray platform support: our own PY-11 fixtures were captured in a Linux container because there is no Windows
  wheel. Confirm macOS and Linux from the memray README; the action must skip cleanly on Windows runners.
- memray `stats --json` field names: take them from the existing real fixtures under `tests/fixtures/real/`, not from docs.
- Lighthouse 13: exact CLI flags for JSON output, headless Chrome flags in CI (`--no-sandbox` only when not root),
  and one run per page covering all six FE checks.
- Where Lighthouse points: a preview URL, or a local static server over the client's build output.

## Plan

1. Composite action `owner-c/profile-upload` with inputs `repository_id`, `command` (Python entry or test command),
   `url` (Lighthouse target), `artifacts` (list). Steps: run tool, presign, PUT repo.zip, PUT artifacts, PUT manifest last.
2. Three runners behind it: `py-spy` (speedscope), `memray` (memray_stats), `lighthouse` (lighthouse). Each skips
   with a clear message when its prerequisites are missing.
3. AWS first: OIDC provider plus a role limited to `lambda:InvokeFunction` on `owner-c-presign`, in the selected Region.
   Confirm `NORMALIZERS` accepts `speedscope`, `memray_stats` and `lighthouse`. Inspect AWS state only after the user agrees.
4. Test on a real client-like repo in a clean GitHub runner: positive and negative, plus a hostile-input case
   (tool missing, empty profile, oversized artifact).
5. Iterate, then verify against the pushed PR and the deployed stack.

## Open decisions

- CODE-C6.1 (owner-a): its artifact handler is a skeleton and uses a different S3 key layout
  (`artifacts/<repo>/<scan_id>/<sha>/<name>`) from owner-c (`uploads/<repo>/<sha>/`). It is on the separate
  Owner A track; leave it out until that track agrees to reuse the owner-c action or align the layout.
- Python entry point: py-spy and memray need a command that exercises the code. Likely the client's test command
  or a named entry script; needs a decision on the default.
- Lighthouse target: preview URL vs local static server.

## Sources

- [py-spy README](https://github.com/adamchainz/py-spy/blob/master/README.md)
- [py-spy reference](https://pydevtools.com/handbook/reference/py-spy/)
- [memray stats docs](https://fossies.org/linux/memray/docs/stats.rst)
- [configure-aws-credentials action](https://github.com/marketplace/actions/configure-aws-credentials-action-for-github-actions)
- [OIDC for AWS in GitHub Actions](https://oneuptime.com/blog/post/2026-01-25-github-actions-oidc-aws/markdown)
- [Lighthouse CI Docker image notes on chrome-flags](https://hub.docker.com/r/pixboost/lighthouse-ci-cli/)
