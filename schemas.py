from typing import Literal

from pydantic import BaseModel, Field, model_validator

Category = Literal["code_quality", "security", "test_coverage"]
Severity = Literal["critical", "high", "medium", "low"]


class Finding(BaseModel):
    category: Category
    severity: Severity
    confidence: Literal["high", "medium", "low"]
    title: str = Field(min_length=1)
    file_path: str
    line_start: int = Field(ge=1)
    line_end: int = Field(ge=1)
    evidence: str = Field(
        min_length=1,
        description=(
            "Verbatim source code from file_path between line_start and line_end. "
            "Copy code only, without explanation, line numbers, backticks or Markdown fences. "
            "Put the explanation, trigger and consequences in impact."
        ),
    )
    impact: str = Field(
        min_length=1,
        description="Explain the trigger, incorrect behavior and consequences; keep source quotes in evidence.",
    )
    suggested_fix: str
    suggested_test: str
    source: str

    @model_validator(mode="after")
    def valid_range(self):
        if self.line_end < self.line_start:
            raise ValueError("line_end must be >= line_start")
        return self


class AgentResult(BaseModel):
    agent: Category
    findings: list[Finding] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)


class AgentReviewStatus(BaseModel):
    agent: Category
    status: Literal["completed", "disabled", "skipped", "failed"]
    generated_findings: int = 0
    accepted_findings: int = 0
    rejected_findings: int = 0
    message: str = ""
    rejection_reasons: list[str] = Field(default_factory=list)


class ReviewReport(BaseModel):
    repository: str
    base_sha: str
    head_sha: str
    mode: str
    metadata: dict = Field(default_factory=dict)
    coverage: dict = Field(default_factory=dict)
    execution_errors: list[str] = Field(default_factory=list)
    agent_reviews: list[AgentReviewStatus] = Field(default_factory=list)
    reviewed_files: list[str]
    findings: list[Finding]
    limitations: list[str]
