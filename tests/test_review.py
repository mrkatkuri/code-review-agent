import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cli import main
from graph import build_graph, consolidate
from repository import Snapshot, load_snapshot
from schemas import AgentResult, Finding
from tools import run_static


def finding(**overrides):
    values = {
        "category": "code_quality",
        "severity": "high",
        "confidence": "high",
        "title": "Undefined value",
        "file_path": "app.py",
        "line_start": 1,
        "line_end": 1,
        "evidence": "print(missing)",
        "impact": "Raises NameError when invoked",
        "suggested_fix": "Define missing",
        "suggested_test": "Call the function",
        "source": "test",
    }
    return Finding(**(values | overrides))


class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.snapshot = Snapshot(
            "/repo",
            "base",
            "head",
            files={"app.py": "print(missing)\n"},
            changed_lines={"app.py": {1}},
        )

    def test_validation_rejects_hallucinated_and_unchanged_locations(self):
        items = [
            finding(),
            finding(),
            finding(file_path="../outside.py"),
            finding(evidence="invented"),
            finding(line_end=99),
        ]
        valid, limitations = consolidate(self.snapshot, items)
        self.assertEqual(len(valid), 1)
        self.assertEqual(len(limitations), 3)
        self.snapshot.changed_lines["app.py"] = set()
        self.assertEqual(consolidate(self.snapshot, [finding()])[0], [])

    def test_parallel_reviewers_join_once_and_preserve_failure(self):
        called = []

        def reviewer(category, snapshot, static):
            called.append(category)
            if category == "security":
                raise RuntimeError("sensitive provider message")
            return AgentResult(
                agent=category,
                findings=[finding()] if category == "code_quality" else [],
            )

        with patch("graph.run_static", return_value=([], [])):
            result = build_graph(reviewer).invoke(
                {"snapshot": self.snapshot, "results": []}
            )
        self.assertCountEqual(called, ["code_quality", "security", "test_coverage"])
        self.assertEqual(len(result["results"]), 3)
        self.assertEqual(len(result["report"].findings), 1)
        self.assertIn(
            "security: review failed (RuntimeError).", result["report"].limitations
        )
        self.assertNotIn("sensitive", str(result["report"]))

    def test_static_tools_detect_bug_and_security_issue(self):
        self.snapshot.files["app.py"] = (
            'import pickle\nprint(missing)\npickle.loads(b"unsafe")\n'
        )
        self.snapshot.changed_lines["app.py"] = {1, 2, 3}
        findings, limitations = run_static(self.snapshot)
        self.assertEqual(limitations, [])
        self.assertTrue(any(f.source == "ruff" and "F821" in f.title for f in findings))
        self.assertTrue(
            any(f.source == "bandit" and "B301" in f.title for f in findings)
        )

    def test_committed_snapshot_and_cli(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)

            def git(*args):
                return subprocess.run(
                    ["git", "-C", str(repo), *args],
                    check=True,
                    capture_output=True,
                    text=True,
                ).stdout.strip()

            git("init", "-q")
            git("config", "user.name", "Test")
            git("config", "user.email", "test@example.com")
            (repo / "app.py").write_text('print("hello")\n')
            git("add", ".")
            git("commit", "-qm", "base")
            base = git("rev-parse", "HEAD")
            (repo / "app.py").write_text('print("hello")\nprint(missing)\n')
            (repo / "test_app.py").write_text("def test_example():\n    assert True\n")
            git("add", ".")
            git("commit", "-qm", "change")
            (repo / "app.py").write_text("UNCOMMITTED CONTENT\n")
            snapshot = load_snapshot(repo, base, "HEAD")
            self.assertEqual(snapshot.changed_lines["app.py"], {2})
            self.assertNotIn("UNCOMMITTED", snapshot.files["app.py"])
            output = repo / "output"
            status = main(
                [
                    "--repo",
                    str(repo),
                    "--base",
                    base,
                    "--static-only",
                    "--no-history",
                    "--output",
                    str(output),
                    "--fail-on",
                    "low",
                ]
            )
            self.assertEqual(status, 1)
            report = json.loads((output / "review.json").read_text())
            self.assertEqual(report["mode"], "static-only")
            self.assertTrue(any("F821" in f["title"] for f in report["findings"]))
            self.assertIn("LLM review disabled", (output / "review.md").read_text())
            limited = load_snapshot(repo, base, "HEAD", max_chars=1)
            self.assertFalse(limited.files)
            self.assertTrue(any("budget" in item for item in limited.limitations))

    def test_bad_ref_returns_error(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(
                main(["--repo", directory, "--base", "invalid", "--static-only"]), 2
            )

    def test_cli_marks_partial_review_as_failure(self):
        from schemas import ReviewReport

        report = ReviewReport(
            repository="/repo",
            base_sha="base",
            head_sha="head",
            mode="multi-agent",
            reviewed_files=["app.py"],
            findings=[],
            limitations=["security: review failed (RuntimeError)."],
            execution_errors=["security: review failed (RuntimeError)."],
        )
        with (
            tempfile.TemporaryDirectory() as directory,
            patch("cli.load_snapshot", return_value=self.snapshot),
            patch("cli.build_graph") as graph,
        ):
            graph.return_value.invoke.return_value = {"report": report}
            self.assertEqual(
                main(
                    [
                        "--base",
                        "base",
                        "--static-only",
                        "--no-history",
                        "--output",
                        directory,
                    ]
                ),
                2,
            )
            self.assertTrue((Path(directory) / "review.json").exists())

    def test_empty_snapshot_does_not_call_model(self):
        def reviewer(*args):
            self.fail("No model calls expected for empty snapshot")

        snapshot = Snapshot("/repo", "base", "head")
        report = build_graph(reviewer).invoke({"snapshot": snapshot, "results": []})[
            "report"
        ]
        self.assertEqual(report.findings, [])
        self.assertEqual(len(report.limitations), 3)


if __name__ == "__main__":
    unittest.main()
