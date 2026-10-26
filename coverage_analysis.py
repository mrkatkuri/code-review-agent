"""Import coverage.py JSON and report uncovered changed executable lines."""

import json
from pathlib import PurePosixPath

from repository import Snapshot
from schemas import Finding


def attach_coverage(snapshot: Snapshot, raw: str, source_root: str = "") -> None:
    data = json.loads(raw)
    if not isinstance(data, dict) or not isinstance(data.get("files"), dict):
        raise ValueError("Expected coverage.py JSON with a files mapping.")  # noqa: TRY004
    normalized = {}
    for name, entry in data["files"].items():
        if not isinstance(name, str) or not isinstance(entry, dict):
            raise ValueError("Invalid coverage file entry.")  # noqa: TRY004
        path = name.replace("\\", "/")
        if source_root and path.startswith(source_root.rstrip("/") + "/"):
            path = path[len(source_root.rstrip("/")) + 1 :]
        path = PurePosixPath(path).as_posix()
        if path not in snapshot.files:
            continue
        executed, missing = entry.get("executed_lines"), entry.get("missing_lines")
        if (
            not isinstance(executed, list)
            or not isinstance(missing, list)
            or any(
                type(line) is not int
                or line < 1
                or line > len(snapshot.files[path].splitlines())
                for line in executed + missing
            )
            or set(executed) & set(missing)
        ):
            raise ValueError(f"Invalid coverage line data for {path}")
        changed = snapshot.changed_lines[path]
        measured = changed & (set(executed) | set(missing))
        uncovered = sorted(changed & set(missing))
        normalized[path] = {
            "changed_executable_lines": len(measured),
            "covered_changed_lines": len(measured) - len(uncovered),
            "missing_changed_lines": uncovered,
        }
    if not normalized:
        raise ValueError(
            "Coverage report has no files matching the selected review paths. Set --coverage-root for absolute paths."
        )
    total = sum(row["changed_executable_lines"] for row in normalized.values())
    covered = sum(row["covered_changed_lines"] for row in normalized.values())
    snapshot.coverage = {
        "files": normalized,
        "changed_executable_lines": total,
        "covered_changed_lines": covered,
        "changed_line_percent": 100 * covered / total if total else None,
    }
    snapshot.limitations.append(
        "Coverage is caller-supplied line coverage; commit/content provenance is not verified. Files absent from the coverage artifact have unknown coverage. Branch coverage is not assessed."
    )


def coverage_findings(snapshot: Snapshot) -> list[Finding]:
    findings = []
    for path, row in snapshot.coverage.get("files", {}).items():
        missing = row["missing_changed_lines"]
        if not missing:
            continue
        line = missing[0]
        findings.append(
            Finding(
                category="test_coverage",
                severity="medium",
                confidence="high",
                title="Changed executable lines are not covered by supplied tests",
                file_path=path,
                line_start=line,
                line_end=line,
                evidence=snapshot.files[path].splitlines()[line - 1],
                impact=f"The supplied coverage artifact marks changed lines {missing} as unexecuted.",
                suggested_fix="Add tests that execute the changed behavior and assert its result.",
                suggested_test=f"Cover the behavior at lines {missing}, including expected results and failure paths.",
                source="coverage.py",
            )
        )
    return findings
