"""Explicit LangSmith experiment adapter; no service requests at import time."""

import time

from agents import model_reviewer
from graph import build_graph
from repository import Snapshot


def labeled_recall(outputs: dict, reference_outputs: dict) -> dict:
    """Score known issue detection without declaring unlabeled findings false positives."""
    labels = reference_outputs.get("expected", [])
    findings = outputs.get("findings", [])
    matched = 0
    for label in labels:
        matched += any(
            finding["file_path"] == label.get("path", "example.py")
            and finding["line_start"] <= label["line"] <= finding["line_end"]
            and ("source" not in label or finding["source"] == label["source"])
            and ("category" not in label or finding["category"] == label["category"])
            and ("rule" not in label or label["rule"] in finding["title"])
            for finding in findings
        )
    return {
        "key": "labeled_issue_recall",
        "score": matched / len(labels) if labels else None,
        "comment": "Known-issue recall only. Unlabeled findings require human adjudication.",
    }


def execution_success(outputs: dict) -> dict:
    return {"key": "execution_success", "score": not outputs.get("execution_errors")}


def evaluate_dataset(dataset: str, model: str | None, client):
    reviewer = model_reviewer(model) if model else None

    def target(inputs: dict) -> dict:
        path = inputs.get("path", "example.py")
        code = inputs["code"]
        if not isinstance(code, str) or len(code) > 100_000:
            raise ValueError("Dataset code must be text within 100,000 characters.")
        # Reuse path validation before any scanner creates temporary source files.
        from repository import safe_review_path

        if not safe_review_path(path):
            raise ValueError("Dataset path must be a supported relative source path.")
        snapshot = Snapshot(
            f"langsmith:{dataset}",
            "empty",
            "dataset-example",
            files={path: code},
            changed_lines={path: set(range(1, len(code.splitlines()) + 1))},
        )
        started = time.perf_counter()
        report = build_graph(reviewer).invoke({"snapshot": snapshot, "results": []})[
            "report"
        ]
        return {
            "findings": [finding.model_dump() for finding in report.findings],
            "execution_errors": report.execution_errors,
            "latency_seconds": time.perf_counter() - started,
        }

    return client.evaluate(
        target,
        data=dataset,
        evaluators=[labeled_recall, execution_success],
        experiment_prefix="multi-agent-code-review",
        max_concurrency=2,
        metadata={"model": model or "static-only"},
    )
