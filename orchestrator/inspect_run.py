"""Read a run report without invoking a model or executing repository code."""

import argparse
from hashlib import sha256
import json
from pathlib import Path
from typing import Any


def inspect_report(run_dir: Path) -> dict[str, Any]:
    data = json.loads((run_dir / "artifacts.json").read_text(encoding="utf-8"))
    summary = {
        key: data[key] for key in ("workspace", "base_sha", "task_sha256", "final_verdict", "final_action", "error")
        if key in data
    }
    summary["completed"] = bool(data.get("ended_at"))
    summary["rounds"] = []
    for record in data.get("rounds", []):
        index = record["round"]
        if type(index) is not int or index < 1:
            raise ValueError("invalid round number")
        expected = record.get("diff", {}).get("sha256")
        patch = run_dir / f"diff_round_{index}.patch"
        patch_verified = sha256(patch.read_bytes()).hexdigest() == expected if expected else None
        summary["rounds"].append({
            "round": index,
            "patch_verified": patch_verified,
            "check_exit_codes": [check["returncode"] for check in record.get("checks", [])],
            "review": record.get("review_decision"),
        })
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", type=Path)
    args = parser.parse_args()
    try:
        summary = inspect_report(args.run_dir)
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        parser.error(f"could not read run report: {exc}")
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return int(any(item["patch_verified"] is False for item in summary["rounds"]))


if __name__ == "__main__":
    raise SystemExit(main())
