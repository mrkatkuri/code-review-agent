"""Read committed Git snapshots without checking out or executing repository code."""

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath


@dataclass
class Snapshot:
    repository: str
    base_sha: str
    head_sha: str
    files: dict[str, str] = field(default_factory=dict)
    diffs: dict[str, str] = field(default_factory=dict)
    changed_lines: dict[str, set[int]] = field(default_factory=dict)
    tests: dict[str, str] = field(default_factory=dict)
    limitations: list[str] = field(default_factory=list)
    scanners: list[str] = field(default_factory=list)
    coverage: dict = field(default_factory=dict)
    metadata: dict = field(default_factory=dict)


def git(repo: Path, *args: str) -> bytes:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=False,
        capture_output=True,
        timeout=30,
    )
    if result.returncode:
        raise ValueError(result.stderr.decode(errors="replace").strip())
    return result.stdout


def safe_python_path(path: str) -> bool:
    p = PurePosixPath(path)
    return (
        not p.is_absolute()
        and ".." not in p.parts
        and "\\" not in path
        and p.suffix == ".py"
        and not any(
            part
            in {
                ".venv",
                "venv",
                "vendor",
                "node_modules",
                "__pycache__",
                ".git",
                ".data",
            }
            for part in p.parts
        )
    )


def load_snapshot(
    repo: Path, base: str, head: str, max_files: int = 30, max_chars: int = 100_000
) -> Snapshot:
    repo = repo.resolve()
    base_sha = (
        git(repo, "rev-parse", "--verify", "--end-of-options", f"{base}^{{commit}}")
        .decode()
        .strip()
    )
    head_sha = (
        git(repo, "rev-parse", "--verify", "--end-of-options", f"{head}^{{commit}}")
        .decode()
        .strip()
    )
    snapshot = Snapshot(str(repo), base_sha, head_sha)
    names = (
        git(
            repo,
            "diff",
            "--no-ext-diff",
            "--name-only",
            "-z",
            "--diff-filter=ACMRT",
            base_sha,
            head_sha,
            "--",
        )
        .decode()
        .split("\0")
    )
    budget = max_chars
    for path in filter(None, names):
        if not safe_review_path(path):
            snapshot.limitations.append(f"Skipped unsupported or excluded file: {path}")
            continue
        entry = git(repo, "ls-tree", head_sha, "--", path).decode()
        if not entry.startswith("100644 ") and not entry.startswith("100755 "):
            snapshot.limitations.append(f"Skipped non-regular file: {path}")
            continue
        content = git(repo, "show", f"{head_sha}:{path}").decode(errors="replace")
        diff = git(
            repo,
            "diff",
            "--no-ext-diff",
            "--no-textconv",
            "--no-renames",
            "--unified=3",
            base_sha,
            head_sha,
            "--",
            path,
        ).decode(errors="replace")
        if len(snapshot.files) >= max_files or len(content) + len(diff) > budget:
            snapshot.limitations.append(
                f"Skipped file because of review budget: {path}"
            )
            continue
        budget -= len(content) + len(diff)
        snapshot.files[path] = content
        snapshot.diffs[path] = diff
        snapshot.changed_lines[path] = added_lines(diff)
    # Small, bounded set of existing tests gives reviewers behavioral context.
    tree = git(repo, "ls-tree", "-r", "-z", head_sha).decode(errors="replace")
    for entry in tree.split("\0"):
        if "\t" not in entry:
            continue
        metadata, path = entry.split("\t", 1)
        if (
            not metadata.startswith(("100644 ", "100755 "))
            or not safe_python_path(path)
            or not (
                PurePosixPath(path).name.startswith("test_")
                or path.endswith("_test.py")
            )
            or path in snapshot.files
        ):
            continue
        if len(snapshot.tests) >= 10:
            snapshot.limitations.append(
                "Additional test files omitted (10-file context limit)."
            )
            break
        content = git(repo, "show", f"{head_sha}:{path}").decode(errors="replace")
        if len(content) > budget:
            snapshot.limitations.append(
                f"Test context omitted because of budget: {path}"
            )
            continue
        snapshot.tests[path] = content
        budget -= len(content)
    snapshot.limitations.append(
        "Reviews committed Python changes only; deleted files, uncommitted edits, "
        "and broader caller/import context are not analyzed. Tests are not executed; "
        "test suggestions do not represent measured coverage."
    )
    if not snapshot.files:
        snapshot.limitations.append(
            "No reviewable Python files in the selected comparison."
        )
    return snapshot


def safe_review_path(path: str) -> bool:
    return safe_python_path(path) or (
        safe_python_path(str(PurePosixPath(path).with_suffix(".py")))
        and PurePosixPath(path).name.startswith("requirements")
        and PurePosixPath(path).suffix == ".txt"
    )


def added_lines(diff: str) -> set[int]:
    changed = set()
    line = 0
    for row in diff.splitlines():
        match = re.match(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@", row)
        if match:
            line = int(match[1])
        elif line == 0 or row.startswith("\\"):
            continue
        elif row.startswith("+"):
            changed.add(line)
            line += 1
        elif row.startswith(" "):
            line += 1
    return changed
