"""Build a metadata-only scenario catalog from the pinned SWE-bench file."""

import argparse
from collections import defaultdict
from hashlib import sha256
from pathlib import Path
import re

import pyarrow.parquet as parquet

from orchestrator.benchmark.catalog import Scenario
from orchestrator.benchmark.source import COLUMNS, problem_digest

SOURCE_SHA256 = "d4f5a245c75319fa8240c540674958c4d491e82edf274b144d43836bdcbc4567"


def task_topic(problem: str) -> str | None:
    for original_line in problem.splitlines()[:6]:
        line = " ".join(original_line.strip().lstrip("#").split())
        line = re.sub(r"^\[(BUG|MNT|ENH|DOC|FEAT)\]\s*:\s*", "", line, flags=re.IGNORECASE)
        words = line.split()
        if not 4 <= len(words) <= 30 or line.startswith(("<", ">", "[", chr(96) * 3, "|")):
            continue
        if re.match(r"^(describe|bug report|what happened|steps to|reproduc|expected behavior|thanks|hello|hi\b)", line, re.I):
            continue
        if re.search(r"(https?://|[A-Za-z0-9_-]{30,}|token\s*[:=]|password\s*[:=])", line, re.I):
            continue
        title = " ".join(words[:18])
        if len(title) > 140:
            title = title[:141].rsplit(" ", 1)[0]
        return title.rstrip(" .:")
    return None


def sample_scenarios(source: Path, count: int) -> list[Scenario]:
    if sha256(source.read_bytes()).hexdigest() != SOURCE_SHA256:
        raise ValueError("source file hash differs from the pinned SWE-bench artifact")
    table = parquet.read_table(source, columns=COLUMNS)
    candidates = defaultdict(list)
    for index, row in enumerate(table.to_pylist()):
        topic = task_topic(row["problem_statement"])
        regressions = tuple(dict.fromkeys(row["FAIL_TO_PASS"]))
        if not topic or not regressions or len(regressions) > 20:
            continue
        case = Scenario(
            id=row["instance_id"], repository=row["repo"], base_commit=row["base_commit"],
            topic=topic, upstream_created_at=row["created_at"], dataset_row=index,
            problem_sha256=problem_digest(row["problem_statement"]),
            regression_checks=regressions, passing_checks=len(set(row["PASS_TO_PASS"])),
        )
        candidates[case.repository].append(case)
    for values in candidates.values():
        values.sort(key=lambda case: sha256(case.id.encode("utf-8")).digest())
    selected = []
    seen = set()
    while len(selected) < count and any(candidates.values()):
        for repository in sorted(candidates):
            values = candidates[repository]
            while values:
                case = values.pop()
                key = (case.repository, case.topic.casefold())
                if key in seen:
                    continue
                selected.append(case)
                seen.add(key)
                break
            if len(selected) == count:
                return selected
    raise ValueError(f"only {len(selected)} eligible distinct scenarios")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--count", type=int, required=True)
    args = parser.parse_args()
    if args.count < 1:
        parser.error("count must be positive")
    try:
        cases = sample_scenarios(args.source, args.count)
        args.destination.mkdir(parents=True, exist_ok=False)
        for case in cases:
            folder = args.destination / case.repository.replace("/", "__")
            folder.mkdir(exist_ok=True)
            (folder / f"{case.id}.json").write_text(case.model_dump_json(indent=2) + "\n", encoding="utf-8")
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
