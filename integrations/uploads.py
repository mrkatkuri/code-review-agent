"""Read repository ZIPs in memory; never extract paths or execute uploaded code."""

import difflib
import hashlib
import io
import stat
import zipfile
from pathlib import PurePosixPath

from repository import Snapshot, safe_review_path

MAX_ARCHIVE_BYTES = 20 * 1024 * 1024
MAX_EXPANDED_BYTES = 50 * 1024 * 1024


def load_upload(
    data: bytes,
    name: str = "repository.zip",
    max_files: int = 30,
    max_chars: int = 100_000,
) -> Snapshot:
    if len(data) > MAX_ARCHIVE_BYTES:
        raise ValueError("ZIP exceeds the 20 MB upload limit.")
    snapshot = Snapshot(
        f"upload:{PurePosixPath(name).name}", "empty", hashlib.sha256(data).hexdigest()
    )
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            entries = archive.infolist()
            if (
                len(entries) > 5000
                or sum(item.file_size for item in entries) > MAX_EXPANDED_BYTES
            ):
                raise ValueError("ZIP exceeds expanded size or entry limits.")
            paths = [
                PurePosixPath(item.filename) for item in entries if not item.is_dir()
            ]
            for path in paths:
                if path.is_absolute() or ".." in path.parts or "\\" in str(path):
                    raise ValueError("ZIP contains an unsafe path.")
            # GitHub archives and typical exports wrap the repository in one folder.
            prefix = (
                paths[0].parts[0]
                if paths
                and all(
                    len(p.parts) > 1 and p.parts[0] == paths[0].parts[0] for p in paths
                )
                else None
            )
            budget = max_chars
            seen = set()
            for item in entries:
                if item.is_dir():
                    continue
                path = PurePosixPath(item.filename)
                path = PurePosixPath(*path.parts[1:]) if prefix else path
                path = path.as_posix()
                if path in seen:
                    raise ValueError("ZIP contains duplicate normalized paths.")
                seen.add(path)
                if stat.S_ISLNK(item.external_attr >> 16):
                    raise ValueError("ZIP symlinks are not supported.")
                if not safe_review_path(path):
                    continue
                if len(snapshot.files) >= max_files or item.file_size > budget:
                    snapshot.limitations.append(
                        f"Skipped upload file because of review budget: {path}"
                    )
                    continue
                content = archive.read(item).decode("utf-8")
                diff = "".join(
                    difflib.unified_diff(
                        [],
                        content.splitlines(keepends=True),
                        fromfile="/dev/null",
                        tofile=path,
                    )
                )
                if len(content) + len(diff) > budget:
                    snapshot.limitations.append(
                        f"Skipped upload file because of review budget: {path}"
                    )
                    continue
                budget -= len(content) + len(diff)
                snapshot.files[path] = content
                snapshot.diffs[path] = diff
                snapshot.changed_lines[path] = set(
                    range(1, len(content.splitlines()) + 1)
                )
    except (zipfile.BadZipFile, UnicodeError, RuntimeError) as exc:
        raise ValueError("Invalid, encrypted, or non-UTF-8 repository ZIP.") from exc
    snapshot.limitations.append(
        "Uploaded snapshot reviewed against an empty baseline; all selected lines are considered new. No code or tests were executed."
    )
    if not snapshot.files:
        snapshot.limitations.append("No reviewable files found in upload.")
    return snapshot
