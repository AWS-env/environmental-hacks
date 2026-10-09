"""Registry of artifact normalizers, keyed by the artifact name clients upload."""

NORMALIZERS = {}


def normalize_all(raw_artifacts: dict, files) -> dict:
    """{artifact name: raw JSON} -> {profiler: {repo path: normalized data}} for the connector."""
    unknown = set(raw_artifacts) - set(NORMALIZERS)
    if unknown:
        raise ValueError(f"unknown artifact type(s): {sorted(unknown)}")
    return {NORMALIZERS[name].PROFILER: NORMALIZERS[name].normalize(raw, files)
            for name, raw in raw_artifacts.items() if raw}
