"""Run the INF-01 detector over a contract input payload."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .inf01 import CHECK_ID, EvaluationError, evaluate


def main(argv=None):
    parser = argparse.ArgumentParser(description=f"Evaluate an {CHECK_ID} contract v1 input payload.")
    parser.add_argument("input", type=Path, help="path to a contract v1 input JSON file")
    parser.add_argument("-o", "--output", type=Path, help="write the result JSON here instead of stdout")
    args = parser.parse_args(argv)

    try:
        payload = json.loads(args.input.read_text())
    except (OSError, json.JSONDecodeError) as error:
        print(f"invalid input file: {error}", file=sys.stderr)
        return 2

    try:
        from shared.contracts.validation import ContractError, validate, validate_pair
    except ImportError as error:
        print(f"run from the repository root so 'shared' is importable: {error}", file=sys.stderr)
        return 1

    try:
        validate(payload)
    except ContractError as error:
        print(f"input is not a valid contract payload: {error}", file=sys.stderr)
        return 2

    try:
        result = evaluate(payload)
    except EvaluationError as error:
        print(f"cannot evaluate {CHECK_ID}: {error}", file=sys.stderr)
        return 1

    try:
        validate_pair(payload, result)
    except ContractError as error:
        print(f"detector produced an invalid result: {error}", file=sys.stderr)
        return 1

    text = json.dumps(result, indent=2) + "\n"
    if args.output:
        args.output.write_text(text)
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
