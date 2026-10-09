# Public G01 verification evidence

The committed deployment summary contains historical package hashes and aggregate smoke results only. It removes AWS project identity, human/profile identifiers and concrete resource names. It does not identify a deployed build of the current PR commit.

Per-check receipts are explicitly sanitized derivatives, not byte-identical S3 readbacks. `artifact.sha256` retains the original private-pair hash for historical provenance; `public_pair_sha256` hashes the canonical public input/result pair. Metadata identifiers are placeholders. Raw receipts remain local and are not committed. Public receipt validation establishes contract validity and local reproduction, not independent live AWS acquisition or current-commit deployment.

DB-05/DB-16 public-source demos use static_candidate mode. Runtime confirmation needs authorized correlated CloudWatch events; hub ingestion/readback and independent review remain outstanding. Synthetic data is labelled synthetic, and no measured environmental savings are invented.
