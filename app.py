"""Single-user Streamlit review dashboard. Run: streamlit run app.py."""

import os
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv

from agents import model_reviewer
from coverage_analysis import attach_coverage
from graph import build_graph
from history import ReviewHistory
from integrations.github import GitHubClient
from integrations.uploads import load_upload
from report import markdown
from repository import load_snapshot
from scanners import SCANNERS
from schemas import ReviewReport


def main():
    load_dotenv(Path(__file__).resolve().parent / ".env")
    st.set_page_config(page_title="Code Review Agent", layout="wide")
    st.title("Code Review Agent")
    st.write(
        "Review Python changes with code quality, security, and test coverage specialists."
    )
    history = ReviewHistory()
    with st.sidebar:
        st.header("Review history")
        rows = history.list()
        if rows:
            selected = st.selectbox(
                "Saved reviews",
                [row["id"] for row in rows],
                format_func=lambda value: next(
                    f"{r['created_at'][:19]} · {r['repository']}"
                    for r in rows
                    if r["id"] == value
                ),
            )
            if st.button("Open saved review"):
                st.session_state["report"] = history.get(selected).model_dump_json()
        else:
            st.caption("Completed reviews appear here.")
        st.caption(
            "Reports and source excerpts are stored locally in .data/reviews.sqlite3."
        )
    source = st.radio(
        "Source", ["GitHub pull request", "Repository ZIP", "Local Git repository"]
    )
    with st.form("review"):
        pr_url, upload, repo, base, head = "", None, ".", "HEAD~1", "HEAD"
        if source == "GitHub pull request":
            pr_url = st.text_input(
                "GitHub PR URL", placeholder="https://github.com/owner/repo/pull/142"
            )
        elif source == "Repository ZIP":
            upload = st.file_uploader("Repository ZIP (up to 20 MB)", type=["zip"])
        else:
            repo = st.text_input("Local repository path", value=".")
            base = st.text_input("Base commit or branch", value="HEAD~1")
            head = st.text_input("Head commit or branch", value="HEAD")
        static_only = st.checkbox("Static analysis only (no model calls)", value=True)
        model = st.text_input("Review model", value=os.getenv("REVIEW_MODEL", ""))
        scanners = st.multiselect("Additional scanners (must be installed)", SCANNERS)
        max_files = st.number_input(
            "Maximum changed files", min_value=1, max_value=200, value=30
        )
        max_chars = st.number_input(
            "Source context character budget",
            min_value=1000,
            max_value=500000,
            value=100000,
            step=1000,
        )
        coverage = st.file_uploader("Optional coverage.py JSON", type=["json"])
        coverage_root = st.text_input("Coverage source root to strip (optional)")
        st.caption(
            "Model reviews send selected code to OpenAI. Uploads are reviewed as full snapshots. Tests are never executed by the dashboard."
        )
        submitted = st.form_submit_button("Run review", type="primary")
    if submitted:
        try:
            if not static_only and (not os.getenv("OPENAI_API_KEY") or not model):
                raise ValueError(
                    "Set OPENAI_API_KEY in .env and choose a review model."
                )
            with st.spinner("Collecting source and running reviewers…"):
                if source == "GitHub pull request":
                    snapshot = GitHubClient.from_environment().load_pr(
                        pr_url, int(max_files), int(max_chars)
                    )
                elif source == "Repository ZIP":
                    if upload is None:
                        raise ValueError("Choose a repository ZIP.")
                    snapshot = load_upload(
                        upload.getvalue(), upload.name, int(max_files), int(max_chars)
                    )
                else:
                    snapshot = load_snapshot(
                        Path(repo), base, head, int(max_files), int(max_chars)
                    )
                snapshot.scanners = scanners
                if coverage is not None:
                    attach_coverage(
                        snapshot, coverage.getvalue().decode(), coverage_root
                    )
                reviewer = None if static_only else model_reviewer(model)
                report = build_graph(reviewer).invoke(
                    {"snapshot": snapshot, "results": []}
                )["report"]
                history.save(report)
                st.session_state["report"] = report.model_dump_json()
        except ValueError as exc:
            st.error(str(exc))
        except Exception as exc:  # noqa: BLE001 - show sanitized errors without source or credentials
            st.error(
                f"Review could not complete ({type(exc).__name__}). Check input, installed scanners, and credentials."
            )
    if "report" not in st.session_state:
        return
    report = ReviewReport.model_validate_json(st.session_state["report"])
    st.subheader(report.metadata.get("title", "Review results"))
    st.caption(f"{report.repository} · {report.head_sha} · {report.mode}")
    columns = st.columns(3)
    for column, category, label in zip(
        columns,
        ("code_quality", "security", "test_coverage"),
        ("Code issues", "Security risks", "Test gaps"),
        strict=True,
    ):
        column.metric(label, sum(f.category == category for f in report.findings))
    if report.agent_reviews:
        st.subheader("Model reviewer execution")
        st.dataframe(
            [
                {
                    "Reviewer": review.agent,
                    "Status": review.status,
                    "Generated": review.generated_findings,
                    "Accepted": review.accepted_findings,
                    "Rejected": review.rejected_findings,
                }
                for review in report.agent_reviews
            ],
            hide_index=True,
        )
        st.caption(
            "Category counts above include static scanner results. Each finding identifies its source."
        )
        if any(review.status == "disabled" for review in report.agent_reviews):
            st.info(
                "Model reviewers were disabled for this report. Run a new review with static-only mode unchecked to use all three AI reviewers."
            )
        for review in report.agent_reviews:
            if review.rejected_findings:
                st.warning(
                    f"{review.agent}: {review.message} "
                    f"Generated {review.generated_findings}, accepted {review.accepted_findings}, rejected {review.rejected_findings}."
                )
                with st.expander(f"{review.agent} rejection reasons"):
                    for reason in review.rejection_reasons:
                        st.write(reason)
    if report.execution_errors:
        st.warning(
            "Review incomplete: some execution steps failed. See limitations below."
        )
    if report.coverage:
        percent = report.coverage.get("changed_line_percent")
        if percent is not None:
            st.metric("Changed executable line coverage", f"{percent:.1f}%")
    severities = st.multiselect(
        "Show severities",
        ["critical", "high", "medium", "low"],
        default=["critical", "high", "medium", "low"],
    )
    for finding in report.findings:
        if finding.severity not in severities:
            continue
        with st.expander(
            f"[{finding.severity.upper()}] {finding.title} · {finding.file_path}:{finding.line_start}"
        ):
            st.caption(
                f"{finding.category} · {finding.confidence} confidence · {finding.source}"
            )
            st.code(finding.evidence, language="python")
            st.write(finding.impact)
            st.write("Suggested fix:", finding.suggested_fix)
            st.write("Suggested test:", finding.suggested_test)
    if not report.findings:
        st.info("No findings reported within this scope. Review the limitations below.")
    with st.expander("Review scope and limitations", expanded=True):
        for item in report.limitations:
            st.write(f"• {item}")
    st.download_button(
        "Download Markdown", markdown(report), "review.md", "text/markdown"
    )
    st.download_button(
        "Download JSON",
        report.model_dump_json(indent=2),
        "review.json",
        "application/json",
    )
    pr = report.metadata.get("pull_request")
    if pr:
        st.write(f"Publish a summary comment to {pr}")
        if st.button("Publish / update GitHub summary"):
            try:
                link = GitHubClient.from_environment().publish_summary(pr, report)
                st.success(f"Summary published: {link}")
            except ValueError as exc:
                st.error(str(exc))


if __name__ == "__main__":
    main()
