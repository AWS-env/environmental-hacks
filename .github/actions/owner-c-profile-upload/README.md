# owner-c-profile-upload

Runs py-spy, memray and Lighthouse on **your own** GitHub runner and uploads the results so owner C's detectors
(PY-01, PY-05, PY-11, FE-02/10/11/13/15/16) can confirm their findings with real runtime evidence.
Nothing you upload is ever executed on our side; we only parse it.

## Use it

Add this job to a workflow in your repository (it must be a repository we have allowlisted, see "Onboarding"):

```yaml
permissions:
  contents: read
  id-token: write          # lets the job prove its identity to AWS; no secrets to store

jobs:
  profile:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7
      - uses: actions/setup-python@v7
        with: { python-version: '3.12' }
      - uses: actions/setup-node@v4
        with: { node-version: '22' }
      # install your own dependencies here (pip install -r requirements.txt, npm ci, ...)
      - uses: AWS-env/environmental-hacks/.github/actions/owner-c-profile-upload@main
        with:
          artifacts: speedscope,memray_stats,lighthouse
          python-args: -m pytest tests/        # what to run after `python`, or a script: app.py
          lighthouse-url: https://your-preview.example.com
```

Ask for only the artifacts you want: `speedscope` (py-spy), `memray_stats` (memray), `lighthouse`.
A tool that cannot run is skipped with a warning, and its checks report `unavailable`, never "clean".

## Inputs

| Input | Default | Meaning |
| --- | --- | --- |
| `artifacts` | required | comma list of `speedscope`, `memray_stats`, `lighthouse` |
| `python-args` | empty | arguments after `python` for py-spy and memray; needed for those two |
| `lighthouse-url` | empty | a reachable URL (your preview deployment); needed for Lighthouse |
| `role-arn` | owner C upload role | role assumed through GitHub OIDC; it can only invoke `owner-c-presign` |
| `region` | `ap-south-1` | region of the owner C stack |
| `repository-id` | `github:<owner>/<repo>` | identity the results are filed under |
| `commit-sha` | `github.sha` | full 40-character commit |

## What gets uploaded

`repo.zip` of your checkout (without `.git`, `node_modules`, virtualenvs, `__pycache__` or any `.env*` file),
the artifact JSON files, and a manifest sent last. Each artifact is capped at 50 MB. Presigned URLs are never printed.
Uploads go through 15-minute presigned URLs; the job never receives long-lived credentials.

## Limits

- memray runs on Linux and macOS runners only (no Windows build).
- py-spy and memray profile the command you give them, so `python-args` should exercise the code you care about
  (your test suite is a good default). Test files themselves are left out of the analysis.
- In Docker, py-spy needs `--cap-add SYS_PTRACE`; the hosted `ubuntu-latest` runner needs nothing extra.
- Pull requests from forks cannot get an OIDC token, so run this on pushes and same-repository pull requests.

## Onboarding (done by us, once per client repository)

The role trusts only repositories we list. GitHub identifies repositories by an immutable subject, so read it first:

```bash
gh api repos/<owner>/<repo>/actions/oidc/customization/sub
# -> {"use_immutable_subject": true, "sub_claim_prefix": "repo:<owner>@<owner_id>/<repo>@<repo_id>"}
```

Then append `<sub_claim_prefix>:*` to the `ClientRepoSubs` parameter of the `owner-c-python-detectors` stack
(`cdk/owner-c/python-detectors.yaml`, ap-south-1) in a reviewed PR and update the stack; the role
`owner-c-client-upload` then trusts that repository. Repositories with `use_immutable_subject: false` use
`repo:<owner>/<repo>:*`. The role's only permission is invoking `owner-c-presign`. Do not edit the role by hand in IAM:
the next stack update would overwrite it.
