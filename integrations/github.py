"""GitHub REST ingestion and explicit, idempotent summary comment publishing."""

import base64
import json
import os
import re
import shutil
import subprocess
from datetime import UTC, datetime
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from report import markdown
from repository import Snapshot, added_lines, safe_python_path, safe_review_path
from schemas import ReviewReport

MARKER = "<!-- code-review-agent:summary -->"


def parse_pr(url: str) -> tuple[str, str, int]:
    match = re.fullmatch(
        r"https://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)/pull/([1-9]\d*)/?",
        url,
    )
    if not match or any(part in {".", ".."} for part in match.groups()[:2]):
        raise ValueError("Expected https://github.com/OWNER/REPO/pull/NUMBER")
    return match[1], match[2], int(match[3])


class GitHubClient:
    def __init__(self, token: str | None = None):
        self.token = token.strip() if token else None
        self.authentication_source = "explicit_token" if self.token else "anonymous"

    @classmethod
    def from_environment(cls):
        """Prefer configured tokens; otherwise reuse the local gh credential store.

        Credentials remain in memory and are never written to .env or reports.
        Explicit invalid credentials are not silently replaced by another identity.
        """
        for name in ("GITHUB_TOKEN", "GH_TOKEN"):
            token = os.getenv(name, "").strip()
            if token:
                client = cls(token)
                client.authentication_source = name
                return client
        if shutil.which("gh"):
            try:
                result = subprocess.run(
                    ["gh", "auth", "token", "--hostname", "github.com"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                    check=False,
                )
                if result.returncode == 0 and result.stdout.strip():
                    client = cls(result.stdout.strip())
                    client.authentication_source = "gh_cli"
                    return client
            except (OSError, subprocess.TimeoutExpired):
                pass
        return cls()

    def error_message(self, error: HTTPError) -> str:
        """Classify bounded GitHub error data without echoing server text/secrets."""
        headers = error.headers or {}
        try:
            payload = json.loads(error.read(65536))
            message = (
                str(payload.get("message", "")).lower()
                if isinstance(payload, dict)
                else ""
            )
        except (ValueError, OSError):
            message = ""
        remaining = headers.get("X-RateLimit-Remaining")
        rate_limited = (
            remaining == "0"
            or error.code == 429
            or "rate limit" in message
            or bool(headers.get("Retry-After"))
        )
        auth = (
            "Requests are anonymous. Set GITHUB_TOKEN/GH_TOKEN or run gh auth login."
            if not self.token
            else "Requests are authenticated; inspect token access if the problem persists."
        )
        if error.code in (403, 429) and rate_limited:
            waits = []
            reset = headers.get("X-RateLimit-Reset")
            if remaining == "0" and reset:
                try:
                    stamp = datetime.fromtimestamp(int(reset), tz=UTC)
                    waits.append(
                        f"Primary limit resets at {stamp:%Y-%m-%d %H:%M:%S UTC}."
                    )
                except (ValueError, OverflowError, OSError):
                    pass
            retry = headers.get("Retry-After")
            if retry and retry.isdigit():
                waits.append(f"Retry after at least {int(retry)} seconds.")
            if not waits:
                waits.append(
                    "Wait at least 60 seconds before retrying; increase the delay if it recurs."
                )
            return (
                f"GitHub API rate limit exceeded (HTTP {error.code}). "
                + " ".join(waits)
                + " "
                + auth
            )
        if error.code == 401:
            return "GitHub authentication failed (HTTP 401). Check or replace GITHUB_TOKEN/GH_TOKEN, or renew gh auth login."
        if error.code == 403:
            return (
                "GitHub access forbidden (HTTP 403). Verify repository access, token permissions, "
                "organization approval and SSO authorization. " + auth
            )
        if error.code == 404:
            return (
                "GitHub resource not found (HTTP 404). Check the PR URL and access to private repositories. "
                + auth
            )
        return f"GitHub API request failed (HTTP {error.code}). " + auth

    def request(self, path: str, method: str = "GET", body=None):
        if not path.startswith("/repos/") and path != "/user":
            raise ValueError("Unsupported GitHub API path")
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "code-review-agent",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        payload = None if body is None else json.dumps(body).encode()
        if payload is not None:
            headers["Content-Type"] = "application/json"
        try:
            with urlopen(
                Request(
                    "https://api.github.com" + path,
                    data=payload,
                    headers=headers,
                    method=method,
                ),
                timeout=30,
            ) as response:
                raw = response.read(8 * 1024 * 1024 + 1)
                if len(raw) > 8 * 1024 * 1024:
                    raise ValueError("GitHub API response exceeds size limit.")
                return json.loads(raw)
        except HTTPError as exc:
            raise ValueError(self.error_message(exc)) from None
        except URLError:
            raise ValueError("GitHub API connection failed.") from None

    def pages(self, path: str, max_pages: int = 30):
        for page in range(1, max_pages + 1):
            rows = self.request(f"{path}?per_page=100&page={page}")
            yield from rows
            if len(rows) < 100:
                return
        raise ValueError("GitHub pagination limit reached; review scope is too large.")

    def content(self, repo_path: str, path: str, sha: str) -> str:
        data = self.request(
            f"{repo_path}/contents/{quote(path, safe='/')}?ref={quote(sha, safe='')}"
        )
        if (
            data.get("type") != "file"
            or data.get("encoding") != "base64"
            or data.get("size", 0) > 1_000_000
        ):
            raise ValueError(f"Unsupported or oversized GitHub file: {path}")
        return base64.b64decode(data["content"]).decode("utf-8")

    def load_pr(
        self, url: str, max_files: int = 30, max_chars: int = 100_000
    ) -> Snapshot:
        owner, repo, number = parse_pr(url)
        endpoint = f"/repos/{owner}/{repo}"
        pr = self.request(f"{endpoint}/pulls/{number}")
        head, base = pr["head"]["sha"], pr["base"]["sha"]
        compare = self.request(f"{endpoint}/compare/{base}...{head}")
        snapshot = Snapshot(
            url,
            compare["merge_base_commit"]["sha"],
            head,
            metadata={"pull_request": url, "title": pr["title"], "base_tip_sha": base},
        )
        head_repo = pr["head"].get("repo")
        full_name = head_repo.get("full_name") if head_repo else None
        if full_name is not None and not re.fullmatch(
            r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", full_name
        ):
            raise ValueError("Invalid head repository name.")
        source_endpoint = f"/repos/{full_name}" if full_name else endpoint
        budget = max_chars
        for row in self.pages(f"{endpoint}/pulls/{number}/files"):
            path = row["filename"]
            if row["status"] == "removed" or not safe_review_path(path):
                snapshot.limitations.append(
                    f"Skipped deleted or unsupported PR file: {path}"
                )
                continue
            patch = row.get("patch")
            if not patch:
                snapshot.limitations.append(
                    f"Skipped PR file without a complete API patch: {path}"
                )
                continue
            if len(snapshot.files) >= max_files or len(patch) > budget:
                snapshot.limitations.append(
                    f"Skipped PR file because of review budget: {path}"
                )
                continue
            content = self.content(source_endpoint, path, head)
            # GitHub can truncate patches; verify additions before using their locations.
            changed = added_lines(patch)
            if len(changed) != row["additions"]:
                snapshot.limitations.append(
                    f"Skipped PR file with a truncated API patch: {path}"
                )
                continue
            if len(content) + len(patch) > budget:
                snapshot.limitations.append(
                    f"Skipped PR file because of review budget: {path}"
                )
                continue
            budget -= len(content) + len(patch)
            snapshot.files[path], snapshot.diffs[path] = content, patch
            snapshot.changed_lines[path] = changed
        tree = self.request(f"{source_endpoint}/git/trees/{head}?recursive=1")
        if tree.get("truncated"):
            snapshot.limitations.append(
                "GitHub tree truncated; existing test context may be incomplete."
            )
        for item in tree.get("tree", []):
            path = item["path"]
            if (
                item.get("mode") not in {"100644", "100755"}
                or not safe_python_path(path)
                or not (
                    path.rsplit("/", 1)[-1].startswith("test_")
                    or path.endswith("_test.py")
                )
                or path in snapshot.files
            ):
                continue
            if len(snapshot.tests) >= 10:
                snapshot.limitations.append(
                    "Additional tests omitted (10-file context limit)."
                )
                break
            if item.get("size", 0) > budget:
                snapshot.limitations.append(
                    f"Test context omitted because of budget: {path}"
                )
                continue
            content = self.content(source_endpoint, path, head)
            if len(content) <= budget:
                snapshot.tests[path] = content
                budget -= len(content)
        current = self.request(f"{endpoint}/pulls/{number}")
        if current["head"]["sha"] != head or current["base"]["sha"] != base:
            raise ValueError("Pull request changed while fetching; rerun the review.")
        snapshot.limitations.append(
            "GitHub API review uses merge-base diff and bounded context. Deleted files are excluded. Repository tests were not executed."
        )
        if not snapshot.files:
            snapshot.limitations.append("No reviewable files found in PR.")
        return snapshot

    def publish_summary(self, url: str, report: ReviewReport) -> str:
        if not self.token:
            raise ValueError("GITHUB_TOKEN is required for publishing.")
        owner, repo, number = parse_pr(url)
        endpoint = f"/repos/{owner}/{repo}"
        pr = self.request(f"{endpoint}/pulls/{number}")
        if (
            report.repository != url
            or pr["head"]["sha"] != report.head_sha
            or (
                report.metadata.get("base_tip_sha") is not None
                and pr["base"]["sha"] != report.metadata["base_tip_sha"]
            )
        ):
            raise ValueError(
                "Report does not match the current PR head; rerun before publishing."
            )
        login = self.request("/user")["login"]
        body = MARKER + "\n" + markdown(report)
        if len(body) > 60_000:
            body = (
                body[:59_800]
                + "\n\nReport truncated; download the complete review artifacts."
            )
        for comment in self.pages(f"{endpoint}/issues/{number}/comments"):
            if MARKER in comment.get("body", "") and comment["user"]["login"] == login:
                result = self.request(
                    f"{endpoint}/issues/comments/{comment['id']}",
                    "PATCH",
                    {"body": body},
                )
                return result["html_url"]
        result = self.request(
            f"{endpoint}/issues/{number}/comments", "POST", {"body": body}
        )
        return result["html_url"]
