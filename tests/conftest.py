"""Keep offline tests from consulting a developer's GitHub credential store."""

import pytest


@pytest.fixture(autouse=True)
def isolate_github_credentials(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "test-only-token")
    monkeypatch.delenv("GH_TOKEN", raising=False)
