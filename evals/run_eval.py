"""Small offline scanner regression set; optional model mode for manual quality review."""

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from dotenv import load_dotenv

from agents import model_reviewer
from graph import build_graph
from repository import Snapshot


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--model", help="Optional live model; otherwise evaluates static analysis only"
    )
    parser.add_argument(
        "--langsmith-dataset",
        help="Run and record an experiment against an existing LangSmith dataset",
    )
    parser.add_argument("--output", type=Path, default=Path("eval-results.json"))
    args = parser.parse_args()
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
    if args.langsmith_dataset:
        from langsmith import Client

        from evals.langsmith_eval import evaluate_dataset

        evaluate_dataset(args.langsmith_dataset, args.model, Client())
        print("LangSmith experiment completed. View results in your LangSmith project.")
        return 0
    reviewer = model_reviewer(args.model) if args.model else None
    results, hits, total = [], 0, 0
    for case in json.loads(Path(__file__).with_name("cases.json").read_text()):
        snapshot = Snapshot(
            f"eval:{case['name']}",
            "empty",
            case["name"],
            files={"example.py": case["code"]},
            changed_lines={
                "example.py": set(range(1, len(case["code"].splitlines()) + 1))
            },
        )
        started = time.perf_counter()
        report = build_graph(reviewer).invoke({"snapshot": snapshot, "results": []})[
            "report"
        ]
        matched = sum(
            any(
                f.source == label["source"]
                and f.line_start == label["line"]
                and label["rule"] in f.title
                for f in report.findings
            )
            for label in case["expected"]
        )
        hits += matched
        total += len(case["expected"])
        failures = report.execution_errors
        passed = matched == len(case["expected"]) and not failures
        if not case["expected"] and report.findings:
            passed = False
        results.append(
            {
                "case": case["name"],
                "latency_seconds": time.perf_counter() - started,
                "passed": passed,
                "expected_matched": matched,
                "report": report.model_dump(),
            }
        )
    args.output.write_text(
        json.dumps(
            {
                "mode": "model" if args.model else "static-only",
                "labeled_issue_recall": hits / total if total else None,
                "note": "Tiny seeded scanner regression set, not a real-PR quality benchmark. Unlabeled findings need human adjudication.",
                "cases": results,
            },
            indent=2,
        )
    )
    print(
        f"{sum(r['passed'] for r in results)}/{len(results)} evaluation cases passed. Output: {args.output}"
    )
    return 0 if all(r["passed"] for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
