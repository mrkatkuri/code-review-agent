"""Review changed code for correctness and maintainability."""

from .base import BaseReviewAgent


class CodeQualityAgent(BaseReviewAgent):
    category = "code_quality"
    focus = (
        "Find logic errors, incorrect edge cases, resource leaks, error-handling defects "
        "and material anti-patterns. Prioritize concrete incorrect behavior over style. "
        "Explain the input or execution path that triggers each issue. "
        "Leave vulnerability-only findings to the security reviewer; focus on correctness "
        "and maintainability rather than duplicating security scanner results."
    )
