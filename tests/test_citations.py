from unittest.mock import patch

import pytest

from citations import source_evidence
from graph import build_graph, consolidate
from report import markdown
from repository import Snapshot
from schemas import AgentResult, Finding


@pytest.mark.parametrize(
    "quote",
    [
        'values[symbol] = float(position["value"])',
        '`values[symbol] = float(position["value"])` overwrites an earlier position.',
        '```python\n    values[symbol] = float(position["value"])\n```',
        '54:     values[symbol] = float(position["value"])',
    ],
)
def test_source_citation_wrappers_preserve_exact_code(quote):
    code = '    values[symbol] = float(position["value"])'
    assert source_evidence(quote, [code], 54) == code


@pytest.mark.parametrize(
    "quote",
    [
        "This overwrites an earlier position.",
        '`values[symbol] += float(position["value"])`',
        '53:     values[symbol] = float(position["value"])',
        " ",
        "```python\n\n```",
    ],
)
def test_citation_verification_rejects_unquoted_invented_or_misnumbered_code(quote):
    assert (
        source_evidence(quote, ['    values[symbol] = float(position["value"])'], 54)
        is None
    )


def test_multiline_citation_allows_dedent_without_changing_tokens():
    code = ["    if value:", "        return value"]
    assert source_evidence("if value:\n    return value", code, 3) == "\n".join(code)
    assert source_evidence("if other:\n    return value", code, 3) is None


def issue(category, evidence, line=2):
    return Finding(
        category=category,
        severity="medium",
        confidence="high",
        title=f"{category}: duplicate positions are not handled",
        file_path="positions.py",
        line_start=line,
        line_end=line,
        evidence=evidence,
        impact="Repeated symbols lose their previous values.",
        suggested_fix="Accumulate position values.",
        suggested_test="Assert that two positions of 250 and 750 sum to 1000.",
        source=f"llm:{category}",
    )


def snapshot():
    return Snapshot(
        "/repo",
        "base",
        "head",
        files={
            "positions.py": "def accumulate(values, symbol, value):\n    values[symbol] = value\n    return values\n"
        },
        changed_lines={"positions.py": {1, 2, 3}},
    )


def test_real_response_pattern_survives_consolidation_for_both_reviewers():
    def reviewer(category, source, static):
        if category == "security":
            return AgentResult(agent=category)
        evidence = "The loop assigns `values[symbol] = value`, replacing the previous position. The supplied test covers only unique symbols."
        return AgentResult(agent=category, findings=[issue(category, evidence)])

    with patch("graph.run_static", return_value=([], [])):
        report = build_graph(reviewer).invoke({"snapshot": snapshot(), "results": []})[
            "report"
        ]
    assert {f.category for f in report.findings} == {"code_quality", "test_coverage"}
    assert all(f.evidence == "    values[symbol] = value" for f in report.findings)
    assert report.execution_errors == []
    by_agent = {review.agent: review for review in report.agent_reviews}
    assert by_agent["security"].generated_findings == 0
    for category in ("code_quality", "test_coverage"):
        status = by_agent[category]
        assert status.status == "completed"
        assert (
            status.generated_findings,
            status.accepted_findings,
            status.rejected_findings,
        ) == (1, 1, 0)
    assert "| code_quality | completed | 1 | 1 | 0 |" in markdown(report)


def test_quote_from_a_different_line_or_file_cannot_validate_a_finding():
    valid, reasons = consolidate(snapshot(), [issue("code_quality", "`return values`")])
    assert not valid
    assert "matching source evidence" in reasons[0]
    invalid_file = issue("code_quality", "`values[symbol] = value`").model_copy(
        update={"file_path": "other.py"}
    )
    assert consolidate(snapshot(), [invalid_file])[0] == []


def test_all_rejected_findings_are_visible_in_report():
    def reviewer(category, source, static):
        return AgentResult(
            agent=category, findings=[issue(category, "invented source")]
        )

    with patch("graph.run_static", return_value=([], [])):
        report = build_graph(reviewer).invoke({"snapshot": snapshot(), "results": []})[
            "report"
        ]
    assert not report.findings
    for review in report.agent_reviews:
        assert (
            review.generated_findings,
            review.accepted_findings,
            review.rejected_findings,
        ) == (1, 0, 1)
        assert "all were rejected" in review.message
        assert "matching source evidence" in review.rejection_reasons[0]


def test_static_only_distinguishes_disabled_reviewers_from_empty_results():
    with patch("graph.run_static", return_value=([], [])):
        report = build_graph().invoke({"snapshot": snapshot(), "results": []})["report"]
    assert {review.status for review in report.agent_reviews} == {"disabled"}
    assert all(review.generated_findings == 0 for review in report.agent_reviews)


def test_dashboard_exposes_all_rejected_review_instead_of_clean_result(
    tmp_path, monkeypatch
):
    from pathlib import Path

    from streamlit.testing.v1 import AppTest

    from schemas import AgentReviewStatus, ReviewReport

    application = Path(__file__).resolve().parent.parent / "app.py"
    report = ReviewReport(
        repository="/repo",
        base_sha="base",
        head_sha="head",
        mode="multi-agent",
        reviewed_files=["positions.py"],
        findings=[],
        limitations=[],
        agent_reviews=[
            AgentReviewStatus(
                agent="code_quality",
                status="completed",
                generated_findings=2,
                rejected_findings=2,
                message="Model produced findings, but all were rejected during validation.",
                rejection_reasons=[
                    "Rejected finding without matching source evidence: example"
                ],
            )
        ],
    )
    monkeypatch.chdir(tmp_path)
    app = AppTest.from_file(str(application))
    app.session_state["report"] = report.model_dump_json()
    app.run(timeout=15)
    assert not app.exception
    assert any(
        "Generated 2, accepted 0, rejected 2" in warning.value
        for warning in app.warning
    )
    assert app.dataframe[0].value["Rejected"].iloc[0] == 2
