"""Opt-in coverage execution in a restricted Docker container at an exact commit."""

import io
import json
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from uuid import uuid4

from coverage_analysis import attach_coverage
from repository import Snapshot, git


def measure_coverage(
    snapshot: Snapshot, image: str = "code-review-coverage:local"
) -> None:
    if snapshot.repository.startswith(("https://", "upload:")):
        raise ValueError("Docker coverage currently requires a local Git repository.")
    if shutil.which("docker") is None:
        raise ValueError(
            "Docker is required for --run-coverage. Build Dockerfile.coverage first."
        )
    archive = git(
        Path(snapshot.repository), "archive", "--format=zip", snapshot.head_sha
    )
    name = f"code-review-coverage-{uuid4().hex}"
    with tempfile.TemporaryDirectory(prefix="review-coverage-") as directory:
        root = Path(directory).resolve()
        with zipfile.ZipFile(io.BytesIO(archive)) as files:
            if sum(item.file_size for item in files.infolist()) > 50 * 1024 * 1024:
                raise ValueError("Coverage snapshot exceeds 50 MB.")
            for item in files.infolist():
                path = PurePosixPath(item.filename)
                if path.is_absolute() or ".." in path.parts or "\\" in str(path):
                    raise ValueError("Unsafe Git archive path.")
                # Reject symlinks and avoid following any archive-controlled link.
                if ((item.external_attr >> 16) & 0o170000) == 0o120000:
                    raise ValueError(
                        "Coverage snapshots containing symlinks are not supported."
                    )
                target = root / path
                if item.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(files.read(item))
        # The random temporary directory must be traversable by the non-root container user.
        root.chmod(0o755)
        command = [
            "docker",
            "run",
            "--rm",
            "--pull=never",
            "--name",
            name,
            "--network=none",
            "--read-only",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges",
            "--pids-limit=128",
            "--memory=512m",
            "--cpus=1",
            "--user=65534:65534",
            "--tmpfs=/tmp:rw,nosuid,size=128m",
            "--mount",
            f"type=bind,source={root},target=/workspace,readonly",
            image,
        ]
        try:
            result = subprocess.run(
                command, capture_output=True, text=True, timeout=180, check=False
            )
            if result.returncode:
                raise ValueError(
                    "Docker coverage failed: tests must pass and dependencies must already exist in the trusted coverage image."
                )
            data = json.loads(result.stdout)
            attach_coverage(snapshot, json.dumps(data), "/workspace")
            snapshot.limitations = [
                item
                for item in snapshot.limitations
                if not item.startswith("Coverage is caller-supplied")
            ]
            snapshot.limitations.append(
                "Line coverage measured by pytest in a restricted Docker container at the reviewed head commit. Branch coverage is not assessed."
            )
            snapshot.metadata["coverage_head_sha"] = snapshot.head_sha
        except subprocess.TimeoutExpired:
            raise ValueError("Docker coverage exceeded the 180-second limit.") from None
        finally:
            subprocess.run(
                ["docker", "rm", "--force", name],
                capture_output=True,
                timeout=15,
                check=False,
            )
