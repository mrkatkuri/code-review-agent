"""Run installed analyzers against temporary copies, ignoring repository configs."""

import json
import subprocess
import sys
import tempfile
from pathlib import Path

from repository import Snapshot
from schemas import Finding


def run_static(snapshot: Snapshot) -> tuple[list[Finding], list[str]]:
    findings, limitations = [], []
    if not snapshot.files:
        return findings, limitations
    with tempfile.TemporaryDirectory(prefix="code-review-") as directory:
        root = Path(directory).resolve()
        for name, content in snapshot.files.items():
            target = root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content)
        commands = {
            "ruff": [
                "ruff",
                "check",
                "--isolated",
                "--no-cache",
                "--select",
                "E4,E7,E9,F",
                "--output-format",
                "json",
                ".",
            ],
            "bandit": ["bandit", "-r", ".", "-f", "json", "-q"],
        }
        for tool, args in commands.items():
            try:
                result = subprocess.run(
                    [sys.executable, "-m", *args],
                    cwd=root,
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=60,
                )
                if result.returncode not in (0, 1):
                    limitations.append(f"{tool} failed (exit {result.returncode}).")
                    continue
                data = json.loads(result.stdout)
                if tool == "bandit":
                    for error in data.get("errors", []):
                        limitations.append(
                            f"Bandit could not analyze: {error.get('filename', 'unknown file')}"
                        )
                rows = data if tool == "ruff" else data.get("results", [])
                for row in rows:
                    filename = Path(row["filename"])
                    if not filename.is_absolute():
                        filename = root / filename
                    path = filename.relative_to(root).as_posix()
                    line = (
                        row["location"]["row"] if tool == "ruff" else row["line_number"]
                    )
                    if line not in snapshot.changed_lines.get(path, set()):
                        continue
                    code = row["code"] if tool == "ruff" else row["test_id"]
                    message = row["message"] if tool == "ruff" else row["issue_text"]
                    findings.append(
                        Finding(
                            category="code_quality" if tool == "ruff" else "security",
                            severity="low"
                            if tool == "ruff"
                            else row["issue_severity"].lower(),
                            confidence="high"
                            if tool == "ruff"
                            else row["issue_confidence"].lower(),
                            title=f"{code}: {message}",
                            file_path=path,
                            line_start=line,
                            line_end=line,
                            evidence=snapshot.files[path].splitlines()[line - 1],
                            impact=message,
                            suggested_fix=f"Inspect {code} and correct the flagged code if applicable.",
                            suggested_test="Add a regression test for the affected behavior where applicable.",
                            source=tool,
                        )
                    )
            except (
                OSError,
                subprocess.TimeoutExpired,
                ValueError,
                KeyError,
                IndexError,
            ) as exc:
                limitations.append(f"{tool} did not complete: {type(exc).__name__}.")
    return findings, limitations
