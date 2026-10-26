from schemas import ReviewReport


def markdown(report: ReviewReport) -> str:
    lines = [
        "# Code review report",
        "",
        f"Comparison: `{report.base_sha}` → `{report.head_sha}`",
        f"Mode: {report.mode} · Files: {len(report.reviewed_files)} · Findings: {len(report.findings)}",
        "",
    ]
    if report.execution_errors:
        lines.extend(["**Review incomplete: some execution steps failed.**", ""])
    if report.coverage:
        percent = report.coverage.get("changed_line_percent")
        lines.extend(
            [
                f"Changed executable line coverage: {percent:.1f}%"
                if percent is not None
                else "Changed executable line coverage: no executable changed lines measured.",
                "",
            ]
        )
    for category in ("code_quality", "security", "test_coverage"):
        lines.append(
            f"- {category}: {sum(f.category == category for f in report.findings)} findings"
        )
    lines.append("")
    if report.agent_reviews:
        lines.extend(
            [
                "## Model reviewer execution",
                "",
                "| Reviewer | Status | Generated | Accepted | Rejected |",
                "| --- | --- | ---: | ---: | ---: |",
            ]
        )
        for review in report.agent_reviews:
            lines.append(
                f"| {review.agent} | {review.status} | {review.generated_findings} | {review.accepted_findings} | {review.rejected_findings} |"
            )
        lines.extend(
            ["", "Static scanner findings are listed separately by their source.", ""]
        )
        for review in report.agent_reviews:
            if review.rejected_findings:
                lines.extend([f"**{review.agent}:** {review.message}", ""])
    for finding in report.findings:
        lines.extend(
            [
                f"## [{finding.severity.upper()}] {finding.title}",
                "",
                f"Location: `{finding.file_path}:{finding.line_start}-{finding.line_end}`",
                f"Category: {finding.category} · Confidence: {finding.confidence} · Source: {finding.source}",
                "",
                "Evidence:",
                "```python",
                finding.evidence,
                "```",
                "",
                f"Impact: {finding.impact}",
                "",
                f"Suggested fix: {finding.suggested_fix}",
                "",
                f"Suggested test: {finding.suggested_test}",
                "",
            ]
        )
    if not report.findings:
        lines.extend(
            [
                "No findings reported within the reviewed scope. See limitations below.",
                "",
            ]
        )
    lines.extend(["## Review scope and limitations", ""])
    lines.extend(f"- {item}" for item in report.limitations)
    return "\n".join(lines) + "\n"
