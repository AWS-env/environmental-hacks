"""Generate the per-issue Verification plan comments (docs/VERIFICATION_PLAN.md) for the CI checks.

The case table is built from the committed `fixtures/ci_NN/ci_cases.json`, so the plan posted on an
issue cannot drift from the tests that run. Usage: python ci_plans.py <output-dir> [CI-06 ...]
This file is not collected by unittest discovery (its name does not start with `test`).
"""
import json
import pathlib
import sys

FIXTURES = pathlib.Path(__file__).parent / "fixtures"
DEPENDENCIES_STATIC = ("Dependencies still missing: PyYAML is pinned in `detectors/owner-c/requirements.txt` (the repo CI step "
                       "already installs that file) and bundled in the shared Lambda zip by `scripts/build-owner-c-lambda.sh`.")
DEPENDENCIES_HISTORY = ("Dependencies still missing: the cases use synthetic normalized history (marked synthetic); "
                        "`tests/test_ci_real.py` checks the normalizer and these checks against real GitHub Actions captures "
                        "(psf/black, numpy/numpy, tiangolo/sqlmodel, fastapi/fastapi, 2026-10-09). PyYAML is needed for the "
                        "workflow-file half of CI-12 and CI-18; a client CI step that runs `collector.py` and uploads the "
                        "`ci_history` artifact through the shared presign -> manifest flow is documented, not yet added to any client repository.")
VALIDATE = ("`PYTHONPATH=detectors/owner-c python -m unittest discover -s detectors/owner-c/tests` and "
            "`python -m shared.contracts.verify` (repository root, Python 3.12+, `pip install pyyaml jsonschema`)")


def describe(expect: dict) -> str:
    parts = [f"status `{expect['status']}`"]
    if "evaluated" in expect:
        evaluated = ", ".join(f"`file:{p}`" for p in expect["evaluated"]) or "none"
        parts.append(f"evaluated scope: {evaluated}")
    findings = expect["findings"]
    if findings:
        for f in findings:
            bits = [f"`{f['identity']}`"]
            if "line" in f:
                bits.append(f"evidence line {f['line']}")
            if "field" in f:
                bits.append(f"field `{f['field']}` = {json.dumps(f['value'])}")
            if "confidence" in f:
                bits.append(f"confidence {f['confidence']}")
            parts.append("finding " + ", ".join(bits))
    else:
        parts.append("no findings")
    if expect.get("limitations_contain"):
        parts.append("limitation mentions " + ", ".join(f"\"{t}\"" for t in expect["limitations_contain"]))
    return "; ".join(parts)


def render(check_id: str, plans: dict) -> str:
    folder = check_id.lower().replace("-", "_")
    cases = json.loads((FIXTURES / folder / "ci_cases.json").read_text(encoding="utf-8"))
    plan = plans[check_id]
    rows = [f"| {c['id']} | {c['title']} | {describe(c['expect'])} | "
            f"`tests/fixtures/{folder}/ci_cases.json` · `tests/test_ci_cases.py` |" for c in cases]
    return "\n".join([
        "## Verification plan",
        "",
        f"Check ID: {check_id}",
        f"Supported language/configuration/provider formats: {plan['formats']}",
        f"Required evidence and scope: {plan['evidence']}",
        f"Detection rule and legitimate exceptions: {plan['rule']}",
        f"Semantic identity used for fingerprints: {plan['identity']} Line numbers are not part of the identity; "
        "a repeated anchor in one file gets `#2`, `#3`.",
        f"Context settings affecting evaluation: {plan['context']}",
        f"Unsupported inputs and limitations: {plan['unsupported']}",
        "",
        "| Case ID | Supplied input / condition | Expected findings, evidence and status | Fixture / test |",
        "| --- | --- | --- | --- |",
        *rows,
        "",
        f"Validation command: {VALIDATE}",
        DEPENDENCIES_HISTORY if plan.get("history") else DEPENDENCIES_STATIC,
        f"Reviewer challenge case: {plan['challenge']}",
        "",
        "- [ ] Input/result pair passes shared contract and evidence validation.",
        "- [ ] Tests assert intended behavior, evidence and coverage.",
        "- [ ] Cases catch always-empty and always-flag implementations.",
        "- [ ] Unsupported impact values remain absent.",
        "- [ ] Reviewer reproduced relevant tests on the latest PR revision.",
        "",
    ])


def main(argv):
    plans = json.loads((FIXTURES / "ci_plans.json").read_text(encoding="utf-8"))
    out = pathlib.Path(argv[1])
    out.mkdir(parents=True, exist_ok=True)
    wanted = argv[2:] or sorted(plans)
    for check_id in wanted:
        (out / f"{check_id}.md").write_text(render(check_id, plans), encoding="utf-8")
        print(f"{check_id} -> {out / (check_id + '.md')} (issue #{plans[check_id]['issue']})")


if __name__ == "__main__":
    main(sys.argv)
