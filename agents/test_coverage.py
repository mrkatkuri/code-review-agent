"""Review changed behavior for gaps in the supplied tests."""

from .base import BaseReviewAgent


class TestCoverageAgent(BaseReviewAgent):
    category = "test_coverage"
    focus = (
        "Find specific changed behaviors missing regression, boundary or failure-path tests. "
        "Compare the supplied existing tests with changed behavior. Suggest concrete test "
        "inputs and expected assertions, and identify weak assertions when relevant. "
        "Do not claim measured coverage or assume omitted tests do not exist. "
        "Cite changed production lines for missing tests, or changed test lines for weak assertions."
    )
