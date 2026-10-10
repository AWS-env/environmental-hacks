"""Owner D (runtime ops & AI): one generic adapter for every check module the package exposes.

Checks are discovered from `owner_d.cli.DETECTORS` (or, failing that, any `owner_d` module with
`CHECK_ID` and `evaluate`), so new static checks plug in when they merge. Per module:

- `SUPPORTED_KIND` "telemetry"/"artifact": runtime evidence a repository scan does not collect,
  so the check is reported `unavailable` without calling it;
- a `parse(locator, content)`: the module's own parser decides which files it handles. Files it
  rejects as `Unsupported` (or returns None for) are out of scope; files it declines with
  `NotEvaluated` are out of scope and listed as notes; parse failures stay in scope so the
  detector reports them as limitations;
- otherwise a `run(ctx)` static check over Python (`static.py`): every `.py` file is in scope.

Settings: a module's `REFERENCE_SETTINGS` (the reference values documented in
detectors/owner-d/README.md) go into its contract `context`, plus the few settings whose reference
is repository-specific (REPO_DERIVED). Settings the scanner already passes win. The values used are
listed in the check's notes, so a report shows when a result rests on reference settings.
"""
from __future__ import annotations

import importlib
import json
import pkgutil
from collections import Counter

from scanner.core import REPO_ROOT, CheckRun, evaluate_each, import_path, is_test_path, static_source

OWNER, NAME = "D", "owner-d-python"
RUNTIME_KINDS = {"telemetry": "existing deployment telemetry (e.g. CloudWatch metrics)",
                 "artifact": "a client-produced artifact (e.g. CI test reports or profiles)"}
NOT_PACKAGES = {"setup", "conftest", "noxfile"}  # top-level scripts, not code under test


def repository_packages(files):
    """Top-level Python packages and modules of the scanned repository (`src/` layout too), tests excluded."""
    names = set()
    for path, _ in files:
        if not path.endswith(".py") or is_test_path(path):
            continue
        parts = path.split("/")
        if parts[0] == "src" and len(parts) > 1:
            parts = parts[1:]
        name = parts[0][:-3] if len(parts) == 1 else parts[0]
        if name.isidentifier() and not name.startswith("__") and name not in NOT_PACKAGES:
            names.add(name)
    return sorted(names)


# Required settings whose README reference is "the repo's own packages" rather than a fixed value.
REPO_DERIVED = {"TST-03": {"production_packages": repository_packages}}


def scan_settings(check_id, module, files, explicit):
    """Contract context for one check (references < repository-derived < explicit) and a note on what was applied."""
    reference = dict(getattr(module, "REFERENCE_SETTINGS", None) or {})
    derived = {key: value for key, derive in REPO_DERIVED.get(check_id, {}).items() if (value := derive(files))}
    notes = []
    for label, values in (("reference settings applied (detectors/owner-d/README.md)", reference),
                          ("settings derived from the repository", derived)):
        shown = [f"{key}={json.dumps(value)}" for key, value in values.items() if key not in explicit]
        if shown:
            notes.append(f"{label}: {', '.join(shown)}"[:300])
    return {**reference, **derived, **explicit}, notes


def discover():
    import owner_d

    try:
        from owner_d import cli
        detectors = dict(getattr(cli, "DETECTORS", None) or {})
    except ImportError:
        detectors = {}
    if not detectors:
        for info in pkgutil.iter_modules(owner_d.__path__):
            if info.name in ("cli", "__main__"):
                continue
            module = importlib.import_module(f"owner_d.{info.name}")
            if isinstance(getattr(module, "CHECK_ID", None), str) and callable(getattr(module, "evaluate", None)):
                detectors[module.CHECK_ID] = module
    return detectors


def select_by_parse(module, files):
    """Files the module's parser accepts, plus a count of files it declines to judge, by reason."""
    selected, declined = [], Counter()
    for path, content in files:
        try:
            parsed = module.parse(path, content)
        except Exception as error:
            kind = type(error).__name__
            if kind == "Unsupported":
                continue
            if kind == "NotEvaluated":
                declined[str(error)[:160]] += 1
                continue
            parsed = True  # a parse failure stays in scope; the detector records it as a limitation
        if parsed is not None:
            selected.append((path, content))
    return selected, declined


class OwnerD:
    owner, name = OWNER, NAME

    def run(self, ctx):
        import_path(REPO_ROOT / "detectors" / "owner-d")
        runs, payloads, detectors = [], [], discover()
        for check_id, module in sorted(detectors.items()):
            kind = getattr(module, "SUPPORTED_KIND", "static")
            if kind in RUNTIME_KINDS:
                runs.append(CheckRun(check_id, OWNER, NAME, unavailable=(
                    f"needs {RUNTIME_KINDS[kind]}; a repository scan collects source files only")))
                continue
            notes = []
            if callable(getattr(module, "parse", None)):
                files, declined = select_by_parse(module, ctx.files)
                selection = "accepted by the check's parse()"
                notes = [f"{count} file(s) out of scope: {reason}" for reason, count in declined.most_common(5)]
            elif callable(getattr(module, "run", None)):
                files, selection = [(p, c) for p, c in ctx.files if p.endswith(".py")], "python (.py)"
            else:
                runs.append(CheckRun(check_id, OWNER, NAME, unavailable=(
                    "the scanner cannot tell which evidence this check needs (no parse/run/SUPPORTED_KIND)")))
                continue
            if not files:
                runs.append(CheckRun(check_id, OWNER, NAME, notes=notes, not_applicable=(
                    f"no collected files in scope for this check ({selection})")))
                continue
            context, applied = scan_settings(check_id, module, ctx.files, ctx.context(file_selection=selection))
            payload = ctx.input(check_id, module.DETECTOR_VERSION, context, [static_source(p, c) for p, c in files])
            payloads.append((payload, applied + notes))
        for (payload, notes), run in zip(payloads, evaluate_each(
                OWNER, NAME, [p for p, _ in payloads], lambda p: detectors[p["check_id"]].evaluate(p))):
            run.notes = notes
            runs.append(run)
        return runs
