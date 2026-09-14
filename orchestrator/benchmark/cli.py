"""Inspect repository scenarios and materialize their inputs."""

import argparse
from collections import Counter
import json
from pathlib import Path
import subprocess

from .catalog import load_catalog
from .checkout import prepare_checkout
from .results import compare_outcomes, read_outcomes, record_outcome
from .source import DATASET_URL, read_source_rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, default=Path("benchmark/scenarios"))
    commands = parser.add_subparsers(dest="action", required=True)
    listing = commands.add_parser("list", help="List available scenarios")
    listing.add_argument("--repo", help="Filter by owner/repository")
    listing.add_argument("--match", help="Search scenario IDs, topics and regression selectors")
    listing.add_argument("--limit", type=int, help="Limit the displayed results")
    listing.add_argument("--summary", action="store_true", help="Print counts by repository")
    show = commands.add_parser("show", help="Show scenario provenance and grading metadata")
    show.add_argument("case_id")
    checkout = commands.add_parser("checkout", help="Fetch a scenario's detached Git checkout")
    checkout.add_argument("case_id")
    checkout.add_argument("destination", type=Path)
    prompt = commands.add_parser("prompt", help="Write the original problem statement as a task file")
    prompt.add_argument("case_id")
    prompt.add_argument("dataset", type=Path)
    prompt.add_argument("output", type=Path)
    verify = commands.add_parser("verify", help="Validate all catalog records against the dataset")
    verify.add_argument("dataset", type=Path)
    record = commands.add_parser("record", help="Emit one workflow outcome as JSON")
    record.add_argument("case_id")
    record.add_argument("variant")
    record.add_argument("run_dir", type=Path)
    compare = commands.add_parser("compare", help="Compare paired results from a JSONL file")
    compare.add_argument("results", type=Path)
    compare.add_argument("left")
    compare.add_argument("right")
    commands.add_parser("source", help="Print the pinned dataset URL")
    args = parser.parse_args(argv)
    if args.action == "list" and args.limit is not None and args.limit < 1:
        parser.error("--limit must be positive")

    if args.action == "source":
        print(DATASET_URL)
        return 0
    try:
        if args.action == "compare":
            print(json.dumps(compare_outcomes(read_outcomes(args.results), args.left, args.right), indent=2))
            return 0
        cases = load_catalog(args.catalog)
        if args.action == "list":
            selected = [case for case in cases.values() if not args.repo or case.repository == args.repo]
            if args.match:
                query = args.match.casefold()
                selected = [
                    case for case in selected
                    if any(query in value.casefold() for value in (case.id, case.topic, *case.regression_checks))
                ]
            if args.limit is not None:
                selected = selected[:args.limit]
            if args.summary:
                for repository, count in sorted(Counter(case.repository for case in selected).items()):
                    print(f"{repository}\t{count}")
            else:
                for case in selected:
                    print(f"{case.id}\t{case.topic}")
        elif args.action == "verify":
            read_source_rows(args.dataset, list(cases.values()))
            print(f"Verified {len(cases)} scenarios against the pinned source.")
        else:
            case = cases.get(args.case_id)
            if case is None:
                raise ValueError(f"unknown scenario {args.case_id}")
            if args.action == "show":
                print(json.dumps({**case.model_dump(mode="json"), "pull_request": case.pull_request_url}, indent=2))
            elif args.action == "checkout":
                print(prepare_checkout(case, args.destination))
            elif args.action == "record":
                print(record_outcome(case, args.variant, args.run_dir).model_dump_json())
            elif args.action == "prompt":
                problems = read_source_rows(args.dataset, [case])
                with args.output.open("x", encoding="utf-8") as handle:
                    handle.write(problems[case.id].rstrip() + "\n")
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
