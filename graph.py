import operator
from typing import Annotated, TypedDict

from langgraph.graph import END, START, StateGraph

from citations import source_evidence
from coverage_analysis import coverage_findings
from repository import Snapshot
from scanners import run_optional
from schemas import AgentResult, AgentReviewStatus, Finding, ReviewReport
from tools import run_static


class State(TypedDict, total=False):
    snapshot: Snapshot
    static: list[Finding]
    limitations: list[str]
    results: Annotated[list[AgentResult], operator.add]
    errors: Annotated[list[str], operator.add]
    agent_statuses: Annotated[list[AgentReviewStatus], operator.add]
    report: ReviewReport


def consolidate(
    snapshot: Snapshot, findings: list[Finding], diagnostics: list[dict] | None = None
) -> tuple[list[Finding], list[str]]:
    valid, limitations, seen = [], [], set()

    def reject(finding, reason):
        limitations.append(f"{reason}: {finding.title}")
        if diagnostics is not None:
            diagnostics.append(
                {
                    "source": finding.source,
                    "category": finding.category,
                    "reason": f"{reason}: {finding.title}",
                }
            )

    severity = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    confidence = {"high": 0, "medium": 1, "low": 2}
    for finding in sorted(
        findings, key=lambda f: (severity[f.severity], confidence[f.confidence])
    ):
        content = snapshot.files.get(finding.file_path)
        if (
            content is None
            or finding.line_end > len(content.splitlines())
            or not any(
                line in snapshot.changed_lines.get(finding.file_path, set())
                for line in range(finding.line_start, finding.line_end + 1)
            )
        ):
            reject(finding, "Rejected finding with invalid or unchanged location")
            continue
        evidence = source_evidence(
            finding.evidence,
            content.splitlines()[finding.line_start - 1 : finding.line_end],
            finding.line_start,
        )
        if evidence is None:
            reject(finding, "Rejected finding without matching source evidence")
            continue
        finding = finding.model_copy(update={"evidence": evidence})
        key = (
            finding.file_path,
            finding.line_start,
            finding.category,
            finding.title.casefold().strip(),
        )
        if key not in seen:
            valid.append(finding)
            seen.add(key)
        elif diagnostics is not None:
            diagnostics.append(
                {
                    "source": finding.source,
                    "category": finding.category,
                    "reason": f"Duplicate finding removed: {finding.title}",
                }
            )
    return valid, limitations


def build_graph(reviewer=None):
    def analyze(state):
        findings, limitations = run_static(state["snapshot"])
        extra, extra_limitations = run_optional(state["snapshot"])
        findings.extend(extra)
        findings.extend(coverage_findings(state["snapshot"]))
        all_limitations = limitations + extra_limitations
        errors = [
            item
            for item in all_limitations
            if any(
                phrase in item
                for phrase in ("failed", "did not complete", "could not analyze")
            )
        ]
        return {"static": findings, "limitations": all_limitations, "errors": errors}

    def worker(category):
        def run(state):
            errors = []
            status, message = "completed", "Model review completed."
            if reviewer is None:
                status, message = (
                    "disabled",
                    "Model review disabled; static scanners run separately.",
                )
                result = AgentResult(
                    agent=category,
                    limitations=[
                        f"{category}: LLM review disabled (static-only mode)."
                    ],
                )
            elif not state["snapshot"].files:
                status, message = "skipped", "No reviewable files."
                result = AgentResult(
                    agent=category, limitations=[f"{category}: no reviewable files."]
                )
            else:
                try:
                    result = reviewer(category, state["snapshot"], state["static"])
                    result = AgentResult.model_validate(result)
                    if result.agent != category or any(
                        f.category != category for f in result.findings
                    ):
                        raise ValueError("Reviewer returned a different category")
                    for finding in result.findings:
                        finding.source = f"llm:{category}"
                except Exception as exc:  # noqa: BLE001 - isolate independent provider failures
                    # Provider errors can contain credentials or source; only expose the type.
                    result = AgentResult(
                        agent=category,
                        limitations=[
                            f"{category}: review failed ({type(exc).__name__})."
                        ],
                    )
                    errors = list(result.limitations)
                    status, message = "failed", result.limitations[0]
            return {
                "results": [result],
                "errors": errors,
                "agent_statuses": [
                    AgentReviewStatus(
                        agent=category,
                        status=status,
                        generated_findings=len(result.findings),
                        message=message,
                    )
                ],
            }

        return run

    def combine(state):
        snapshot = state["snapshot"]
        findings = list(state["static"])
        limitations = snapshot.limitations + state["limitations"]
        for result in sorted(state["results"], key=lambda r: r.agent):
            findings.extend(result.findings)
            limitations.extend(result.limitations)
        diagnostics = []
        valid, rejected = consolidate(snapshot, findings, diagnostics)
        statuses = []
        for status in sorted(state.get("agent_statuses", []), key=lambda s: s.agent):
            source = f"llm:{status.agent}"
            accepted = sum(f.source == source for f in valid)
            reasons = [row["reason"] for row in diagnostics if row["source"] == source]
            message = status.message
            if (
                status.status == "completed"
                and status.generated_findings
                and not accepted
            ):
                message = "Model produced findings, but all were rejected during validation. Inspect rejection reasons."
            statuses.append(
                status.model_copy(
                    update={
                        "accepted_findings": accepted,
                        "rejected_findings": len(reasons),
                        "rejection_reasons": reasons,
                        "message": message,
                    }
                )
            )
        return {
            "report": ReviewReport(
                repository=snapshot.repository,
                base_sha=snapshot.base_sha,
                head_sha=snapshot.head_sha,
                mode="multi-agent" if reviewer else "static-only",
                reviewed_files=list(snapshot.files),
                metadata=snapshot.metadata,
                coverage=snapshot.coverage,
                execution_errors=state.get("errors", []),
                agent_reviews=statuses,
                findings=valid,
                limitations=limitations + rejected,
            )
        }

    graph = StateGraph(State)
    graph.add_node("static", analyze)
    graph.add_edge(START, "static")
    for category in ("code_quality", "security", "test_coverage"):
        graph.add_node(category, worker(category))
        graph.add_edge("static", category)
    graph.add_node("combine", combine)
    graph.add_edge(["code_quality", "security", "test_coverage"], "combine")
    graph.add_edge("combine", END)
    return graph.compile()
