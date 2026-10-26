import io
import json
import tempfile
import zipfile
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from coverage_analysis import attach_coverage, coverage_findings
from history import ReviewHistory
from integrations.github import MARKER, GitHubClient, parse_pr
from integrations.uploads import load_upload
from repository import Snapshot
from scanners import run_optional
from schemas import ReviewReport


def archive(files):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as zipped:
        for name, content in files.items():
            zipped.writestr(name, content)
    return output.getvalue()


def report():
    return ReviewReport(
        repository="https://github.com/org/repo/pull/1",
        base_sha="base",
        head_sha="head",
        mode="static-only",
        reviewed_files=["app.py"],
        findings=[],
        limitations=[],
    )


def test_upload_full_snapshot_and_limits():
    snapshot = load_upload(
        archive(
            {
                "project/app.py": "print(missing)\n",
                "project/.env": "secret",
                "project/readme.md": "hello",
            }
        )
    )
    assert set(snapshot.files) == {"app.py"}
    assert snapshot.changed_lines["app.py"] == {1}
    limited = load_upload(archive({"app.py": "print(1)\n"}), max_chars=1)
    assert not limited.files
    assert any("budget" in item for item in limited.limitations)


@pytest.mark.parametrize(
    "path", ["../app.py", "/app.py", "project/../../app.py", "project\\app.py"]
)
def test_upload_rejects_traversal(path):
    with pytest.raises(ValueError, match="unsafe"):
        load_upload(archive({path: "print(1)"}))


def test_upload_rejects_symlink_and_duplicate():
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as zipped:
        link = zipfile.ZipInfo("app.py")
        link.external_attr = 0o120777 << 16
        zipped.writestr(link, "outside.py")
    with pytest.raises(ValueError, match="symlink"):
        load_upload(output.getvalue())
    with pytest.raises(ValueError, match="Invalid"):
        load_upload(b"not a zip")


def test_coverage_intersects_changed_executable_lines():
    snapshot = Snapshot(
        "/repo",
        "base",
        "head",
        files={"app.py": "x = 1\ny = 2\nz = 3\n"},
        changed_lines={"app.py": {2, 3}},
    )
    attach_coverage(
        snapshot,
        json.dumps(
            {
                "files": {
                    "/workspace/app.py": {
                        "executed_lines": [1, 2],
                        "missing_lines": [3],
                    }
                }
            }
        ),
        "/workspace",
    )
    assert snapshot.coverage["changed_line_percent"] == 50
    findings = coverage_findings(snapshot)
    assert len(findings) == 1
    assert findings[0].line_start == 3
    with pytest.raises(ValueError, match="no files matching"):
        attach_coverage(snapshot, '{"files": {}}')
    with pytest.raises(ValueError, match="Invalid coverage"):
        attach_coverage(
            snapshot,
            json.dumps(
                {"files": {"app.py": {"executed_lines": [99], "missing_lines": []}}}
            ),
        )


def test_history_round_trip_and_parameterized_ids():
    with tempfile.TemporaryDirectory() as directory:
        history = ReviewHistory(Path(directory) / "reviews.sqlite3")
        review_id = history.save(report())
        assert history.get(review_id) == report()
        assert history.list()[0]["id"] == review_id
        with pytest.raises(ValueError, match="not found"):
            history.get("' OR 1=1 --")


def test_github_pr_uses_merge_base_and_detects_truncated_patch():
    client = GitHubClient()
    pr = {"head": {"sha": "head"}, "base": {"sha": "base"}, "title": "Example"}
    client.request = Mock(
        side_effect=[pr, {"merge_base_commit": {"sha": "merge-base"}}, {"tree": []}, pr]
    )
    client.pages = Mock(
        return_value=iter(
            [
                {
                    "filename": "app.py",
                    "status": "modified",
                    "patch": "@@ -1 +1 @@\n-old\n+new",
                    "additions": 1,
                },
                {
                    "filename": "other.py",
                    "status": "modified",
                    "patch": "@@ -1 +1 @@\n-old\n+new",
                    "additions": 2,
                },
            ]
        )
    )
    client.content = Mock(return_value="new\n")
    snapshot = client.load_pr("https://github.com/org/repo/pull/1")
    assert snapshot.base_sha == "merge-base"
    assert snapshot.files == {"app.py": "new\n"}
    assert any("truncated" in item for item in snapshot.limitations)
    assert snapshot.metadata["base_tip_sha"] == "base"


def test_github_detects_pr_change_during_fetch():
    client = GitHubClient()
    pr = {"head": {"sha": "head"}, "base": {"sha": "base"}, "title": "Example"}
    client.request = Mock(
        side_effect=[
            pr,
            {"merge_base_commit": {"sha": "merge"}},
            {"tree": []},
            pr | {"head": {"sha": "new-head"}},
        ]
    )
    client.pages = Mock(return_value=iter([]))
    with pytest.raises(ValueError, match="changed while fetching"):
        client.load_pr("https://github.com/org/repo/pull/1")


def test_comment_updates_only_own_marker_and_rejects_stale():
    client = GitHubClient("not-a-real-token")
    client.request = Mock(
        side_effect=[
            {"head": {"sha": "head"}},
            {"login": "reviewer"},
            {"html_url": "https://github.com/comment"},
        ]
    )
    client.pages = Mock(
        return_value=iter(
            [
                {"id": 4, "body": MARKER, "user": {"login": "someone-else"}},
                {"id": 5, "body": MARKER, "user": {"login": "reviewer"}},
            ]
        )
    )
    assert (
        client.publish_summary(report().repository, report())
        == "https://github.com/comment"
    )
    assert client.request.call_args.args[1] == "PATCH"
    assert "/comments/5" in client.request.call_args.args[0]
    client.request = Mock(return_value={"head": {"sha": "different"}})
    with pytest.raises(ValueError, match="current PR head"):
        client.publish_summary(report().repository, report())
    with pytest.raises(ValueError):
        parse_pr("https://evil.example/org/repo/pull/1")


def test_optional_scanners_real_radon_and_pylint():
    source = (
        "def complex_function(x):\n"
        + "".join(f"    if x == {i}:\n        return {i}\n" for i in range(12))
        + "    return missing\n"
    )
    snapshot = Snapshot(
        "/repo",
        "base",
        "head",
        files={"app.py": source},
        changed_lines={"app.py": set(range(1, len(source.splitlines()) + 1))},
        scanners=["radon", "pylint"],
    )
    findings, limitations = run_optional(snapshot)
    assert not limitations
    assert {"radon", "pylint"} <= {f.source for f in findings}


def test_dependency_audit_refuses_executable_requirements():
    snapshot = Snapshot(
        "/repo",
        "base",
        "head",
        files={"requirements.txt": "-e git+https://example.com/repo\n"},
        changed_lines={"requirements.txt": {1}},
        scanners=["pip-audit"],
    )
    with patch("scanners.subprocess.run") as run:
        findings, limitations = run_optional(snapshot)
    run.assert_not_called()
    assert not findings
    assert any("failed" in item for item in limitations)


def test_dependency_audit_uses_only_pins_and_parses_vulnerabilities():
    snapshot = Snapshot(
        "/repo",
        "base",
        "head",
        files={"requirements.txt": "example==1.0\n"},
        changed_lines={"requirements.txt": {1}},
        scanners=["pip-audit"],
    )
    output = {
        "dependencies": [
            {
                "name": "example",
                "version": "1.0",
                "vulns": [{"id": "TEST-123", "description": "Example vulnerability"}],
            }
        ]
    }
    with patch(
        "scanners.subprocess.run",
        return_value=Mock(returncode=1, stdout=json.dumps(output)),
    ) as run:
        findings, limitations = run_optional(snapshot)
    assert not limitations
    assert findings[0].source == "pip-audit"
    assert "--disable-pip" in run.call_args.args[0]
    assert "--no-deps" in run.call_args.args[0]


def test_semgrep_parses_results_without_network_rules():
    snapshot = Snapshot(
        "/repo",
        "base",
        "head",
        files={"app.py": "eval(value)\n"},
        changed_lines={"app.py": {1}},
        scanners=["semgrep"],
    )
    output = {
        "results": [
            {
                "path": "app.py",
                "start": {"line": 1},
                "check_id": "python-dynamic-eval",
                "extra": {"message": "Unsafe eval"},
            }
        ]
    }
    with (
        patch("scanners.shutil.which", return_value="/bin/semgrep"),
        patch(
            "scanners.subprocess.run",
            return_value=Mock(returncode=0, stdout=json.dumps(output)),
        ) as run,
    ):
        findings, limitations = run_optional(snapshot)
    assert not limitations
    assert findings[0].source == "semgrep"
    assert "--metrics=off" in run.call_args.args[0]


def test_docker_coverage_uses_restricted_container_and_cleanup():
    from coverage_runner import measure_coverage

    snapshot = Snapshot(
        "/repo",
        "base",
        "head",
        files={"app.py": "x = 1\n"},
        changed_lines={"app.py": {1}},
    )
    output = {"files": {"app.py": {"executed_lines": [1], "missing_lines": []}}}
    with (
        patch("coverage_runner.shutil.which", return_value="/bin/docker"),
        patch("coverage_runner.git", return_value=archive({"app.py": "x = 1\n"})),
        patch(
            "coverage_runner.subprocess.run",
            side_effect=[
                Mock(returncode=0, stdout=json.dumps(output)),
                Mock(returncode=0),
            ],
        ) as run,
    ):
        measure_coverage(snapshot)
    command = run.call_args_list[0].args[0]
    for flag in (
        "--network=none",
        "--read-only",
        "--cap-drop=ALL",
        "--user=65534:65534",
        "--pull=never",
    ):
        assert flag in command
    assert run.call_args_list[-1].args[0][:3] == ["docker", "rm", "--force"]
    assert snapshot.coverage["changed_line_percent"] == 100
    assert snapshot.metadata["coverage_head_sha"] == "head"


def test_docker_coverage_rejects_untrusted_archive_paths():
    from coverage_runner import measure_coverage

    snapshot = Snapshot("/repo", "base", "head")
    with (
        patch("coverage_runner.shutil.which", return_value="/bin/docker"),
        patch("coverage_runner.git", return_value=archive({"../escape.py": "x = 1"})),
        patch("coverage_runner.subprocess.run") as run,
        pytest.raises(ValueError, match="Unsafe"),
    ):
        measure_coverage(snapshot)
    run.assert_not_called()


def test_dashboard_renders_saved_report_and_controls(tmp_path, monkeypatch):
    from streamlit.testing.v1 import AppTest

    application = Path(__file__).resolve().parent.parent / "app.py"
    monkeypatch.chdir(tmp_path)
    app = AppTest.from_file(str(application))
    app.session_state["report"] = report().model_dump_json()
    app.run(timeout=15)
    assert not app.exception
    assert [metric.label for metric in app.metric] == [
        "Code issues",
        "Security risks",
        "Test gaps",
    ]
    assert app.radio[0].value == "GitHub pull request"
    assert len(app.get("download_button")) == 2


def test_github_comment_rejects_changed_base():
    client = GitHubClient("not-a-real-token")
    review = report()
    review.metadata["base_tip_sha"] = "original-base"
    client.request = Mock(
        return_value={"head": {"sha": "head"}, "base": {"sha": "changed-base"}}
    )
    with pytest.raises(ValueError, match="current PR head"):
        client.publish_summary(review.repository, review)
    client.request.assert_called_once()


def test_cli_upload_coverage_history_round_trip(tmp_path):
    from cli import main

    zipped = tmp_path / "repository.zip"
    zipped.write_bytes(archive({"app.py": "value = 1\n"}))
    coverage = tmp_path / "coverage.json"
    coverage.write_text(
        json.dumps({"files": {"app.py": {"executed_lines": [], "missing_lines": [1]}}})
    )
    database = tmp_path / "history.sqlite3"
    status = main(
        [
            "--upload",
            str(zipped),
            "--coverage-json",
            str(coverage),
            "--static-only",
            "--history-db",
            str(database),
            "--output",
            str(tmp_path / "reports"),
            "--fail-on",
            "medium",
        ]
    )
    assert status == 1
    history = ReviewHistory(database)
    saved = history.get(history.list()[0]["id"])
    assert saved.coverage["changed_line_percent"] == 0
    assert saved.findings[0].source == "coverage.py"
    assert (tmp_path / "reports/review.md").exists()
