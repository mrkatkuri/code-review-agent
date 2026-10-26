"""Three specialized reviewers and their model dispatch factory."""

from collections.abc import Callable

from repository import Snapshot
from schemas import AgentResult, Category, Finding

from .code_quality import CodeQualityAgent
from .security import SecurityAgent
from .test_coverage import TestCoverageAgent

__all__ = ["CodeQualityAgent", "SecurityAgent", "TestCoverageAgent", "model_reviewer"]


def model_reviewer(model: str) -> Callable:
    agents = {
        "code_quality": CodeQualityAgent(model),
        "security": SecurityAgent(model),
        "test_coverage": TestCoverageAgent(model),
    }

    def review(
        category: Category, snapshot: Snapshot, static: list[Finding]
    ) -> AgentResult:
        return agents[category].review(snapshot, static)

    return review
