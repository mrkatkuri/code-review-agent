"""Opt-in deterministic scanner adapters. No target dependencies are installed."""

import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from repository import Snapshot
from schemas import Finding

SCANNERS = ("radon", "pylint", "semgrep", "pip-audit")


def make_finding(
    snapshot,
    path,
    line,
    title,
    impact,
    source,
    category="code_quality",
    severity="medium",
):
    return Finding(
        category=category,
        severity=severity,
        confidence="medium",
        title=title,
        file_path=path,
        line_start=line,
        line_end=line,
        evidence=snapshot.files[path].splitlines()[line - 1],
        impact=impact,
        suggested_fix="Inspect the reported issue and correct the affected behavior.",
        suggested_test="Add a regression test for the reported behavior.",
        source=source,
    )


def run_optional(snapshot: Snapshot) -> tuple[list[Finding], list[str]]:
    findings, limitations = [], []
    if not snapshot.files:
        return findings, limitations
    with tempfile.TemporaryDirectory(prefix="review-scanners-") as directory:
        root = Path(directory).resolve()
        for path, content in snapshot.files.items():
            target = root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content)
        python_files = [p for p in snapshot.files if p.endswith(".py")]
        for scanner in snapshot.scanners:
            try:
                if scanner == "radon":
                    from radon.complexity import cc_visit

                    for path in python_files:
                        blocks = cc_visit(snapshot.files[path])
                        blocks += [
                            method
                            for block in list(blocks)
                            for method in getattr(block, "methods", [])
                        ]
                        for block in blocks:
                            changed = sorted(
                                snapshot.changed_lines[path]
                                & set(range(block.lineno, block.endline + 1))
                            )
                            if block.complexity > 10 and changed:
                                findings.append(
                                    make_finding(
                                        snapshot,
                                        path,
                                        changed[0],
                                        f"Complexity {block.complexity}: {block.name}",
                                        "Cyclomatic complexity above 10 increases the number of paths requiring tests.",
                                        "radon",
                                        severity="low",
                                    )
                                )
                elif scanner == "pylint":
                    if not python_files:
                        continue
                    # Explicit empty config prevents uploaded pylint init hooks/plugins.
                    config = root / "review-pylint.rc"
                    config.write_text("[MAIN]\nload-plugins=\ninit-hook=\n")
                    result = subprocess.run(
                        [
                            sys.executable,
                            "-m",
                            "pylint",
                            "--rcfile",
                            str(config),
                            "--output-format=json",
                            "--persistent=n",
                            "--disable=all",
                            "--enable=E,F",
                            *python_files,
                        ],
                        cwd=root,
                        check=False,
                        capture_output=True,
                        text=True,
                        timeout=90,
                    )
                    if result.returncode & (1 | 32) or result.returncode < 0:
                        raise ValueError("Pylint fatal/usage error")
                    for row in json.loads(result.stdout):
                        path, line = row["path"], row["line"]
                        if line in snapshot.changed_lines.get(path, set()):
                            findings.append(
                                make_finding(
                                    snapshot,
                                    path,
                                    line,
                                    f"{row['message-id']}: {row['message']}",
                                    row["message"],
                                    "pylint",
                                )
                            )
                elif scanner == "semgrep":
                    binary = shutil.which("semgrep")
                    if binary is None:
                        raise ValueError("Semgrep is not installed")
                    rules = Path(__file__).resolve().parent / "rules/security.yml"
                    result = subprocess.run(
                        [
                            binary,
                            "scan",
                            "--config",
                            str(rules),
                            "--json",
                            "--metrics=off",
                            "--disable-version-check",
                            "--no-git-ignore",
                            ".",
                        ],
                        cwd=root,
                        check=False,
                        capture_output=True,
                        text=True,
                        timeout=90,
                    )
                    if result.returncode:
                        raise ValueError("Semgrep failed")
                    data = json.loads(result.stdout)
                    if data.get("errors"):
                        limitations.append(
                            "semgrep failed to analyze some files; results are partial."
                        )
                    for row in data.get("results", []):
                        path = row["path"].removeprefix("./")
                        line = row["start"]["line"]
                        if line in snapshot.changed_lines.get(path, set()):
                            findings.append(
                                make_finding(
                                    snapshot,
                                    path,
                                    line,
                                    row["check_id"],
                                    row["extra"]["message"],
                                    "semgrep",
                                    category="security",
                                )
                            )
                elif scanner == "pip-audit":
                    requirements = [
                        p
                        for p in snapshot.files
                        if Path(p).name.startswith("requirements")
                        and p.endswith(".txt")
                    ]
                    if not requirements:
                        limitations.append(
                            "pip-audit: no selected requirements*.txt files; dependency audit not performed."
                        )
                    for path in requirements:
                        pinned, locations = [], {}
                        for line, text in enumerate(
                            snapshot.files[path].splitlines(), 1
                        ):
                            value = text.strip()
                            if not value or value.startswith("#"):
                                continue
                            match = re.fullmatch(
                                r"([A-Za-z0-9][A-Za-z0-9_.-]*)==([A-Za-z0-9][A-Za-z0-9_.+!-]*)",
                                value,
                            )
                            if not match:
                                raise ValueError(
                                    "Only exact name==version requirements without directives are supported"
                                )
                            normalized = re.sub(r"[-_.]+", "-", match[1]).lower()
                            locations[(normalized, match[2])] = line
                            pinned.append(value)
                        if not pinned:
                            continue
                        safe = root / "audit-pinned.txt"
                        safe.write_text("\n".join(pinned))
                        result = subprocess.run(
                            [
                                sys.executable,
                                "-m",
                                "pip_audit",
                                "-r",
                                str(safe),
                                "--no-deps",
                                "--disable-pip",
                                "--cache-dir",
                                str(root / ".audit-cache"),
                                "--progress-spinner",
                                "off",
                                "-f",
                                "json",
                            ],
                            cwd=root,
                            check=False,
                            capture_output=True,
                            text=True,
                            timeout=90,
                        )
                        if result.returncode not in (0, 1):
                            raise ValueError("Dependency audit failed")
                        data = json.loads(result.stdout)
                        for dependency in data["dependencies"]:
                            if dependency.get("skip_reason"):
                                limitations.append(
                                    f"pip-audit failed to audit {dependency['name']}."
                                )
                                continue
                            line = locations.get(
                                (
                                    re.sub(r"[-_.]+", "-", dependency["name"]).lower(),
                                    dependency["version"],
                                )
                            )
                            if line not in snapshot.changed_lines.get(path, set()):
                                continue
                            for vuln in dependency.get("vulns", []):
                                findings.append(
                                    make_finding(
                                        snapshot,
                                        path,
                                        line,
                                        f"{vuln['id']}: vulnerable {dependency['name']}=={dependency['version']}",
                                        vuln.get("description")
                                        or "Known dependency vulnerability.",
                                        "pip-audit",
                                        category="security",
                                    )
                                )
                else:
                    raise ValueError("Unknown scanner")
            except (
                ImportError,
                OSError,
                subprocess.TimeoutExpired,
                ValueError,
                KeyError,
                SyntaxError,
            ) as exc:
                limitations.append(
                    f"{scanner} failed ({type(exc).__name__}); install the optional scanner and check input support."
                )
    return findings, limitations
