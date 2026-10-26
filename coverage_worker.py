"""Trusted entrypoint copied into the dedicated coverage image, never into uploads."""

import json
import os
import subprocess
import sys
from pathlib import Path

os.chdir("/workspace")
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
os.environ["COVERAGE_FILE"] = "/tmp/.coverage"
os.environ["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
os.environ["PYTHONPATH"] = "/workspace"
# Repository pytest config is deliberately ignored; repository conftest/tests still execute.
result = subprocess.run(
    [
        sys.executable,
        "-m",
        "pytest",
        "-c",
        "/dev/null",
        "-p",
        "pytest_cov.plugin",
        "-p",
        "no:cacheprovider",
        "--basetemp=/tmp/pytest",
        "--cov=.",
        "--cov-config=/dev/null",
        "--cov-report=json:/tmp/coverage.json",
        "--cov-report=",
        "/workspace",
    ],
    stdout=sys.stderr,
    stderr=sys.stderr,
    check=False,
)
if result.returncode:
    raise SystemExit(result.returncode)
print(json.dumps(json.loads(Path("/tmp/coverage.json").read_text())))
