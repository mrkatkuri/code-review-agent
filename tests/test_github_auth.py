import io
import json
import subprocess
from email.message import Message
from unittest.mock import Mock, patch
from urllib.error import HTTPError

import pytest

from integrations.github import GitHubClient


def error(status, headers=None, message=""):
    response_headers = Message()
    for name, value in (headers or {}).items():
        response_headers[name] = value
    return HTTPError(
        "https://api.github.com/repos/org/repo/pulls/1",
        status,
        "error",
        response_headers,
        io.BytesIO(json.dumps({"message": message}).encode()),
    )


def test_explicit_token_precedes_cli_and_secondary_environment_token(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "configured-token")
    monkeypatch.setenv("GH_TOKEN", "secondary-token")
    with patch("integrations.github.subprocess.run") as run:
        client = GitHubClient.from_environment()
    assert client.token == "configured-token"
    assert client.authentication_source == "GITHUB_TOKEN"
    run.assert_not_called()


def test_gh_token_environment_is_supported(monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setenv("GH_TOKEN", "secondary-token")
    assert GitHubClient.from_environment().authentication_source == "GH_TOKEN"


def test_logged_in_cli_is_reused_without_exposing_token(monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    with (
        patch("integrations.github.shutil.which", return_value="/bin/gh"),
        patch(
            "integrations.github.subprocess.run",
            return_value=Mock(returncode=0, stdout="stored-token\n"),
        ) as run,
    ):
        client = GitHubClient.from_environment()
    assert client.token == "stored-token"
    assert client.authentication_source == "gh_cli"
    assert run.call_args.args[0] == ["gh", "auth", "token", "--hostname", "github.com"]
    assert "stored-token" not in client.error_message(error(403))


@pytest.mark.parametrize("timeout", [False, True])
def test_unavailable_cli_falls_back_to_anonymous(monkeypatch, timeout):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    with (
        patch("integrations.github.shutil.which", return_value="/bin/gh"),
        patch("integrations.github.subprocess.run") as run,
    ):
        if timeout:
            run.side_effect = subprocess.TimeoutExpired("gh", 5)
        else:
            run.return_value = Mock(returncode=1, stdout="")
        client = GitHubClient.from_environment()
    assert client.token is None
    assert client.authentication_source == "anonymous"


def test_primary_rate_limit_reports_reset_and_anonymous_auth_hint():
    message = GitHubClient().error_message(
        error(
            403,
            {"x-ratelimit-remaining": "0", "x-ratelimit-reset": "1793012400"},
            "API rate limit exceeded",
        )
    )
    assert "rate limit exceeded" in message
    assert "UTC" in message
    assert "anonymous" in message
    assert "gh auth login" in message


def test_secondary_rate_limit_uses_retry_after():
    message = GitHubClient("secret").error_message(
        error(403, {"retry-after": "90"}, "secondary rate limit")
    )
    assert "at least 90 seconds" in message
    assert "authenticated" in message
    assert "secret" not in message


def test_permission_error_is_distinct_and_never_echoes_server_body():
    client = GitHubClient("secret")
    message = client.error_message(
        error(403, {"x-ratelimit-remaining": "100"}, "Resource not accessible secret")
    )
    assert "access forbidden" in message
    assert "token permissions" in message
    assert "rate limit exceeded" not in message
    assert "secret" not in message


@pytest.mark.parametrize("status", [401, 403, 404, 429, 500])
def test_request_surfaces_safe_actionable_http_errors(status):
    client = GitHubClient("secret")
    with (
        patch(
            "integrations.github.urlopen", side_effect=error(status, message="secret")
        ),
        pytest.raises(ValueError) as caught,
    ):
        client.request("/repos/org/repo/pulls/1")
    assert str(status) in str(caught.value)
    assert "secret" not in str(caught.value)
