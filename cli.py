import argparse
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

from dotenv import load_dotenv

from agents import model_reviewer
from coverage_analysis import attach_coverage
from coverage_runner import measure_coverage
from graph import build_graph
from history import ReviewHistory
from integrations.github import GitHubClient
from integrations.uploads import load_upload
from report import markdown
from repository import load_snapshot
from scanners import SCANNERS


def positive(value):
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return number


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Review committed Python changes using three LangGraph reviewers."
    )
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--repo", type=Path, default=Path("."))
    source.add_argument("--github-pr", help="GitHub pull request URL")
    source.add_argument(
        "--upload", type=Path, help="Repository ZIP (full snapshot review)"
    )
    parser.add_argument("--base", help="Base commit or branch")
    parser.add_argument(
        "--head", default="HEAD", help="Head commit or branch (default: HEAD)"
    )
    parser.add_argument(
        "--static-only",
        action="store_true",
        help="Run Ruff and Bandit without model calls",
    )
    parser.add_argument(
        "--model", help="Model identifier; defaults to REVIEW_MODEL from .env"
    )
    parser.add_argument("--output", type=Path, default=Path("reports"))
    parser.add_argument("--max-files", type=positive, default=30)
    parser.add_argument("--max-chars", type=positive, default=100_000)
    parser.add_argument(
        "--fail-on",
        choices=["critical", "high", "medium", "low"],
        help="Exit 1 for findings at or above this severity",
    )
    parser.add_argument("--scanners", nargs="*", choices=SCANNERS, default=[])
    coverage = parser.add_mutually_exclusive_group()
    coverage.add_argument("--coverage-json", type=Path)
    coverage.add_argument(
        "--run-coverage",
        action="store_true",
        help="Execute local repository tests in Docker",
    )
    parser.add_argument(
        "--coverage-root",
        default="",
        help="Path prefix to strip from imported coverage files",
    )
    parser.add_argument("--coverage-image", default="code-review-coverage:local")
    parser.add_argument(
        "--history-db", type=Path, default=Path(".data/reviews.sqlite3")
    )
    parser.add_argument("--no-history", action="store_true")
    parser.add_argument(
        "--publish-comment",
        action="store_true",
        help="Create/update a summary on --github-pr",
    )
    args = parser.parse_args(argv)
    if not args.github_pr and not args.upload and not args.base:
        parser.error("--base is required for local Git comparisons.")
    if args.publish_comment and not args.github_pr:
        parser.error("--publish-comment requires --github-pr.")
    if args.run_coverage and (args.github_pr or args.upload):
        parser.error("--run-coverage requires a local --repo.")
    # Load only this application's .env, never a target repository's configuration.
    load_dotenv(Path(__file__).resolve().parent / ".env")
    model = args.model or os.getenv("REVIEW_MODEL")
    if not args.static_only and (not os.getenv("OPENAI_API_KEY") or not model):
        parser.error(
            "Set OPENAI_API_KEY and REVIEW_MODEL (or --model), or use --static-only."
        )
    try:
        github = GitHubClient.from_environment()
        if args.github_pr:
            snapshot = github.load_pr(args.github_pr, args.max_files, args.max_chars)
        elif args.upload:
            snapshot = load_upload(
                args.upload.read_bytes(),
                args.upload.name,
                args.max_files,
                args.max_chars,
            )
        else:
            snapshot = load_snapshot(
                args.repo, args.base, args.head, args.max_files, args.max_chars
            )
        snapshot.scanners = args.scanners
        if args.coverage_json:
            attach_coverage(
                snapshot, args.coverage_json.read_text(), args.coverage_root
            )
        elif args.run_coverage:
            measure_coverage(snapshot, args.coverage_image)
        reviewer = None if args.static_only else model_reviewer(model)
        report = build_graph(reviewer).invoke({"snapshot": snapshot, "results": []})[
            "report"
        ]
        args.output.mkdir(parents=True, exist_ok=True)
        (args.output / "review.json").write_text(
            report.model_dump_json(indent=2) + "\n"
        )
        (args.output / "review.md").write_text(markdown(report))
        if not args.no_history:
            review_id = ReviewHistory(args.history_db).save(report)
            print(f"Saved review history: {review_id}")
        if args.publish_comment:
            print(f"GitHub summary: {github.publish_summary(args.github_pr, report)}")
    except (
        ValueError,
        OSError,
        TimeoutError,
        subprocess.TimeoutExpired,
        sqlite3.Error,
    ) as exc:
        print(f"Review failed: {exc}", file=sys.stderr)
        return 2
    print(
        f"Reviewed {len(report.reviewed_files)} files; {len(report.findings)} findings."
    )
    print(f"Reports: {args.output / 'review.md'} and {args.output / 'review.json'}")
    for review in report.agent_reviews:
        print(
            f"{review.agent}: {review.status}; generated={review.generated_findings}, "
            f"accepted={review.accepted_findings}, rejected={review.rejected_findings}"
        )
    if report.limitations:
        print("Review limitations are included in both reports.")
    # Incomplete execution must not look like a successful CI review.
    if report.execution_errors:
        return 2
    ranks = ["critical", "high", "medium", "low"]
    if args.fail_on and any(
        ranks.index(f.severity) <= ranks.index(args.fail_on) for f in report.findings
    ):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
