"""Review changed code for security vulnerabilities."""

from .base import BaseReviewAgent


class SecurityAgent(BaseReviewAgent):
    category = "security"
    focus = (
        "Find exploitable injection, unsafe deserialization, credential exposure, "
        "authentication and authorization flaws. Trace untrusted input to sensitive "
        "operations and explain attack prerequisites. Use Bandit findings as supporting "
        "evidence rather than assuming every flagged pattern is exploitable."
    )
