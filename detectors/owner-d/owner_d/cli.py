"""Run an Owner D detector over a contract input payload."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import inf01, inf02, inf04, inf07, inf08, inf09, inf10, llm01, llm02, llm03, llm04, llm05, llm06, llm07, llm08, llm09, llm10, llm11, llm12, llm13, llm14, llm15, llm16, llm17, llm18, obs01, obs02, obs03, obs04, obs05, obs06, obs07, obs08, obs09, obs10, obs11, obs12, obs13, obs14, obs15, obs17, obs18, tst01, tst02, tst03, tst04, tst05, tst06, tst07, tst08, tst09, tst10, tst11, tst12

DETECTORS = {
    inf01.CHECK_ID: inf01,
    inf02.CHECK_ID: inf02,
    inf04.CHECK_ID: inf04,
    inf07.CHECK_ID: inf07,
    inf08.CHECK_ID: inf08,
    inf09.CHECK_ID: inf09,
    inf10.CHECK_ID: inf10,
    llm01.CHECK_ID: llm01,
    llm02.CHECK_ID: llm02,
    llm03.CHECK_ID: llm03,
    llm04.CHECK_ID: llm04,
    llm05.CHECK_ID: llm05,
    llm06.CHECK_ID: llm06,
    llm07.CHECK_ID: llm07,
    llm08.CHECK_ID: llm08,
    llm09.CHECK_ID: llm09,
    llm10.CHECK_ID: llm10,
    llm11.CHECK_ID: llm11,
    llm12.CHECK_ID: llm12,
    llm13.CHECK_ID: llm13,
    llm14.CHECK_ID: llm14,
    llm15.CHECK_ID: llm15,
    llm16.CHECK_ID: llm16,
    llm17.CHECK_ID: llm17,
    llm18.CHECK_ID: llm18,
    obs01.CHECK_ID: obs01,
    obs02.CHECK_ID: obs02,
    obs03.CHECK_ID: obs03,
    obs04.CHECK_ID: obs04,
    obs05.CHECK_ID: obs05,
    obs06.CHECK_ID: obs06,
    obs07.CHECK_ID: obs07,
    obs08.CHECK_ID: obs08,
    obs09.CHECK_ID: obs09,
    obs10.CHECK_ID: obs10,
    obs11.CHECK_ID: obs11,
    obs12.CHECK_ID: obs12,
    obs13.CHECK_ID: obs13,
    obs14.CHECK_ID: obs14,
    obs15.CHECK_ID: obs15,
    obs17.CHECK_ID: obs17,
    obs18.CHECK_ID: obs18,
    tst01.CHECK_ID: tst01,
    tst02.CHECK_ID: tst02,
    tst03.CHECK_ID: tst03,
    tst04.CHECK_ID: tst04,
    tst05.CHECK_ID: tst05,
    tst06.CHECK_ID: tst06,
    tst07.CHECK_ID: tst07,
    tst08.CHECK_ID: tst08,
    tst09.CHECK_ID: tst09,
    tst10.CHECK_ID: tst10,
    tst11.CHECK_ID: tst11,
    tst12.CHECK_ID: tst12,
}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Evaluate an Owner D contract v1 input payload.")
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

    detector = DETECTORS.get(payload.get("check_id"))
    if detector is None:
        print(f"unsupported Owner D check_id: {payload.get('check_id')}", file=sys.stderr)
        return 1

    try:
        result = detector.evaluate(payload)
    except detector.EvaluationError as error:
        print(f"cannot evaluate {detector.CHECK_ID}: {error}", file=sys.stderr)
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
