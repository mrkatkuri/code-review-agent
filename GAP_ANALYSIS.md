# Comparison against the supplied implementation plan

| Requirement | Before this update | Current implementation |
|---|---|---|
| Python + LangGraph + LangChain + OpenAI | Implemented | Preserved |
| Three parallel specialist agents | Implemented | Separate agent classes; deterministic orchestration preserved |
| Local repository review | Committed local diffs | Preserved |
| GitHub PR review | Missing | REST metadata/files, merge-base comparison, bounded test context, revision consistency checks |
| Uploaded repositories | Missing | ZIP full-snapshot review with archive validation and budgets |
| Ruff/Bandit | Implemented | Preserved |
| Pylint/Radon | Missing | Opt-in adapters and install extra |
| Semgrep | Missing | Opt-in adapter, install extra, bundled eval/shell rules; intentionally limited rule coverage |
| pip-audit | Missing | Opt-in audit of exact pins in selected requirements files; no project installation/resolution |
| Test gap suggestions | Implemented | Preserved; measured evidence is now supplied when available |
| pytest/pytest-cov/coverage.py | Missing | Dev extra, application test coverage in CI, artifact import, explicit Docker test measurement for local Git input |
| Prioritized actionable report | Markdown/JSON | Added category counts, coverage summary and source metadata |
| Streamlit dashboard | Missing | Three input modes, counts, filters, findings, downloads, history, explicit summary publishing |
| SQLite history | Missing | Parameterized save/list/load, CLI/UI integration |
| PostgreSQL | Later phase | Deferred as specified in the plan |
| GitHub PR comments (optional) | Missing | Explicit summary create/update; own-author marker matching and stale-revision rejection |
| CI/CD | Missing | GitHub Actions tests, coverage, seeded evaluation, static PR review artifacts |
| Docker | Missing | Dashboard image and restricted coverage execution image |
| Application tests | unittest, mocked model | pytest suite expanded with input adapters, safety boundaries, coverage, persistence, and scanners |
| LangSmith | Not documented | Opt-in tracing plus existing-dataset experiment adapter (known-issue recall, execution success, latency); no hosted evaluation run |
| Evaluation | No dataset | Small seeded regression dataset/runner; real-PR/model quality benchmark remains future work |
| Other languages | Later phase | Python only, as specified for initial release |

The recommended stack is not an assertion of exhaustive security or review coverage. Important boundaries:

- Model reviewers are separate structured calls with different instructions; no autonomous tool loops, iterative fixes, or general inter-agent chat.
- GitHub API pagination, incomplete patches, budgets, deleted files, and omitted context limit scope. Imported coverage must be matched to the revision by the caller.
- Dependency scanning covers selected pinned requirements files only. Transitive dependencies, lockfiles and pyproject declarations are not resolved. Semgrep currently has two local rules, not a comprehensive registry ruleset.
- Existing tests are bounded context. No automatic caller/import retrieval, semantic duplicate resolution, or project-specific test-environment setup.
- The dashboard is single-user and has no authentication, tenancy, queue, or progress streaming. SQLite stores report excerpts; production access controls and retention policies remain application-owner responsibilities.
- Docker artifacts and the coverage runner are implemented; full Docker execution depends on a local Docker engine and a suitable trusted dependency image.
- GitHub reads/comment logic are tested with mocked API responses. A public read-only PR smoke test is separate from model quality evaluation; no live comment or model calls are needed for tests.
- A representative human-labeled PR dataset, precision/recall adjudication, cost/latency measurements, and a comparable single-agent baseline still need to be developed before production-quality claims.

Validation in this workspace:

- Public read-only smoke review: https://github.com/pallets/flask/pull/6168 (6 selected files; Markdown/JSON in `reports/public-pr-review/`). This verifies ingestion/reporting, not the validity of every finding.
- Real Ruff, Bandit, Radon, and Pylint execution tested. pip-audit and Semgrep adapters tested with mocked scanner JSON; no live vulnerability service or Semgrep execution was performed.
- Streamlit dashboard rendered with Streamlit AppTest. Docker is not installed in this environment; runner arguments, archive handling and cleanup are tested with mocks, but image builds/container tests remain unverified.
- No live model calls, LangSmith uploads, GitHub comments, or hosted CI runs were performed.

## Citation validation fix

A live reproduction against portfolio-analyser PR #1 confirmed that the models generated code-quality and test-coverage findings, but returned narrative evidence containing backticked code. Consolidation previously compared the entire narrative against source text and discarded all of these findings.

The fix clarifies the evidence schema/prompt, extracts only exact source quotes from common citation wrappers, and stores canonical source evidence. Claimed paths/ranges and changed-line overlap remain required; invented code and quotes found only elsewhere remain rejected. Per-reviewer execution status and generated/accepted/rejected counts now appear in CLI, JSON, Markdown and dashboard output. Historical saved reports are preserved; rerun a review to obtain the corrected findings and diagnostics.
