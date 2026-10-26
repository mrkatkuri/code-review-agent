# Multi-Agent Code Review Agent

Python 3.12+, LangGraph, LangChain, and OpenAI. Three specialist agents run in parallel: code quality, security, and test coverage. Deterministic scanners provide additional evidence, and the orchestrator validates, deduplicates, and prioritizes the combined findings.

## Install

```bash
uv sync --extra dashboard --extra scanners --extra dev
```

A local `.env` already exists. Set `OPENAI_API_KEY` and `REVIEW_MODEL` for model reviews. `.env.example` documents optional GitHub and LangSmith settings. Do not commit credentials. `--static-only` requires no OpenAI key and makes no model calls.

Optional extras:

- `dashboard`: Streamlit UI.
- `scanners`: Radon, Pylint, and pip-audit.
- `semgrep`: Semgrep (larger optional installation). Enable with `--scanners semgrep`; uses bundled local rules, with metrics disabled.
- `dev`: pytest, pytest-cov, and coverage.py.

Ruff and Bandit are always installed. Model reviews send selected source, diffs, test context, and scanner/coverage evidence to OpenAI. The three agents share the selected model, with separate prompts. Agent analysis is currently a bounded structured call, not an autonomous tool loop.

## Inputs

Local Git comparison (two exact refs, not an automatic merge-base):

```bash
uv run code-review-agent --repo /path/to/repository --base main --head feature-branch
```

GitHub PR (merge-base comparison, supports public PRs and token-authorized private PRs):

```bash
uv run code-review-agent --github-pr https://github.com/OWNER/REPO/pull/142 --static-only
```

Repository ZIP (review selected files against an empty baseline):

```bash
uv run code-review-agent --upload repository.zip --static-only
```

ZIPs are read in memory without extraction. Traversal paths, symlinks, duplicate normalized paths, encrypted files, and oversized archives are rejected. Limits: 20 MB compressed, 50 MB expanded, 5,000 entries. A single wrapper folder is stripped. Unsupported files are ignored. Application modules live in this parent project folder; there is no nested `code_review_agent` package.

Only `.py` and `requirements*.txt` files are selected. Local Git input excludes uncommitted changes and deleted files. Default review limits: 30 changed files, 100,000 source/diff/test characters, up to 10 additional existing test files. `--max-files` and `--max-chars` adjust those limits; character budget is not a price or token guarantee. GitHub patch truncation and skipped files are reported explicitly. API reads are rechecked for PR changes before review.

## Streamlit dashboard

```bash
uv run streamlit run app.py
```

Choose GitHub PR, ZIP upload, or a local Git comparison; select static-only or model mode and optional scanners. The dashboard displays counts by specialist, severity-filtered findings, code evidence, fixes, suggested tests, coverage metrics when supplied, and review limitations. Download JSON/Markdown or open saved review history. GitHub reports offer an explicit publish/update button.

The dashboard is intended for a trusted, single-user environment. It has no authentication or per-user history isolation. Do not expose local repository access or saved source excerpts on a public service without adding access controls.

## Scanners

```bash
uv run code-review-agent --repo /path/to/repo --base HEAD~1 --static-only \
  --scanners radon pylint semgrep pip-audit
```

| Tool | Current integration |
|---|---|
| Ruff | E4/E7/E9/F checks, isolated configuration |
| Bandit | Python security patterns |
| Radon | Cyclomatic complexity above 10 for changed functions/classes |
| Pylint | Error/fatal checks, explicit empty config, no repository plugins/init hooks |
| Semgrep | Bundled rules for dynamic `eval` and subprocess `shell=True`; no remote rule download |
| pip-audit | Exact `name==version` entries in selected requirements files; dependency resolution and pip disabled |

pip-audit queries vulnerability services over the network. It deliberately rejects requirements directives, URLs, editable packages, markers, and unpinned versions; it does not audit pyproject/uv.lock or resolve transitive dependencies. Scanner findings are patterns requiring review, not proof of exploitability. Requested scanner errors produce an incomplete review and exit code 2.

## Measured coverage

Import a coverage.py or pytest-cov JSON artifact generated for the reviewed revision:

```bash
uv run code-review-agent --repo /path/to/repo --base HEAD~1 \
  --coverage-json coverage.json --coverage-root /absolute/source/root --static-only
```

The system intersects changed lines with measured executable lines, computes changed-line coverage, and flags uncovered changed lines. Files absent from the artifact have unknown coverage. Imported artifact commit/content provenance is not verified. Branch coverage is not evaluated. The test agent receives these metrics alongside existing tests and suggests behavioral tests.

For explicit test execution at a local repository's exact reviewed commit:

```bash
docker build -f Dockerfile.coverage -t code-review-coverage:local .
uv run code-review-agent --repo /path/to/repo --base HEAD~1 --run-coverage --static-only
```

This exports the committed snapshot and runs pytest in a non-root, read-only Docker container, with no network, no credentials, dropped capabilities, resource limits, and a 180-second timeout. No image is pulled implicitly. Repository tests/conftest execute inside the container; Docker isolation is not a universal security boundary. The default image contains pytest/pytest-cov/coverage only. For project dependencies, build a trusted custom image derived from the coverage image, then select `--coverage-image IMAGE`. Dependencies are never installed automatically from uploaded source. ZIP/GitHub inputs do not execute tests; use imported coverage for them. Failed tests or missing dependencies fail measurement instead of producing a coverage claim.

## Reports, history, and CI thresholds

Reports are written to `reports/review.md` and `reports/review.json` (overwritten each run); use `--output` to choose a directory. Findings contain category, severity, confidence, code location, exact evidence, impact, suggested fix/test, and source. Invalid locations, ranges without changed lines, and mismatched source evidence are rejected. Citations can contain exact inline code quotes, fenced code, prompt line-number prefixes, or uniformly dedented code; accepted evidence is replaced with the verified source snippet. Matching stays inside the cited line range, with no fuzzy matching or whole-file search. Exact duplicate category/location/title findings are removed; semantic duplicates may remain. Findings sort by severity, then confidence.

SQLite history is stored in `.data/reviews.sqlite3`. Use `--history-db PATH` or `--no-history`. Saved reports contain code excerpts. History stores reports, not full repository snapshots. PostgreSQL is a later extension.

```bash
uv run code-review-agent --repo /path/to/repo --base HEAD~1 --static-only \
  --scanners radon pylint --fail-on high --no-history
```

Exit codes: 0 completed, 1 findings at/above `--fail-on`, 2 invalid input or execution failure. Skipped/unsupported files and disabled model reviewers are explicit limitations. Zero findings does not establish correctness. The CLI, Markdown/JSON reports, and dashboard show each model reviewer's execution status and generated/accepted/rejected finding counts. If a reviewer generated findings that validation rejected, rejection reasons are visible rather than being presented as a clean review. Static analyzer findings identify their own source and are separate from model reviewer counts.

## GitHub comments

GitHub authentication is selected in this order: `GITHUB_TOKEN`, `GH_TOKEN`, then the local GitHub CLI credential store (`gh auth token --hostname github.com`). If none is available, public reads are anonymous and have lower rate limits. Tokens stay in memory; the app never copies CLI credentials into `.env` or reports. Invalid explicit tokens are not silently replaced with another identity.

If you already use GitHub CLI, no additional token is needed:

```bash
gh auth login --hostname github.com
```

For a server or Docker deployment without a logged-in CLI, supply `GITHUB_TOKEN` through `.env`/environment. Read access requires contents/pull-request permissions; summary publishing also requires permission to write issue/PR comments.

Rate-limit errors identify primary reset time or secondary retry delay when GitHub provides them. Permission failures are reported separately. The client does not repeatedly retry rate-limited requests. If you set a token in `.env` after starting Streamlit, restart the app so it uses the new configuration.

```bash
uv run code-review-agent --github-pr https://github.com/OWNER/REPO/pull/142 \
  --static-only --publish-comment
```

Publishing is opt-in. The client creates a summary comment or updates its own existing marked comment, and refuses a stale head/base revision. Reports remain local by default. Comments are summaries, not inline review annotations.

`.github/workflows/review.yml` runs application tests/coverage and offline evaluation, plus a static review job with downloadable reports. The review job installs the trusted base revision and fetches PR files as data. It uses the `pull_request` event, read-only permissions, and no OpenAI secrets. Enable model calls or comment writing only in an appropriately trusted workflow. The workflow requires these project files to exist on the base branch.

## Tests and evaluation

```bash
uv run pytest --cov=. --cov-report=json:coverage.json --cov-report=term-missing
uv run ruff check --isolated .
uv run python evals/run_eval.py
```

Tests cover real local analyzers, Git ingestion, graph joins/failures, agent routing, ZIP safety, mocked GitHub fetching/publishing, coverage interpretation, SQLite persistence, and scanner adapters. `evals/cases.json` is a small seeded offline regression set; `evals/run_eval.py --model MODEL` additionally invokes live reviewers. Live mode sends fixture code to OpenAI and incurs API usage. It is not a substitute for a labeled real-PR benchmark or a single-agent baseline; unlabeled findings require human adjudication.

For optional LangSmith traces, set `LANGSMITH_TRACING=true`, `LANGSMITH_API_KEY`, and `LANGSMITH_PROJECT`. LangChain/LangGraph instrumentation uses these settings; traces can contain source code. An explicit hosted experiment can use an existing LangSmith dataset:

```bash
uv run python evals/run_eval.py --langsmith-dataset YOUR_DATASET --model YOUR_MODEL
```

Dataset examples use inputs `{ "code": "Python source", "path": "example.py" }` and reference outputs `{ "expected": [{ "path": "example.py", "line": 2, "category": "security" }] }`. Optional label fields `source` and `rule` restrict matching. The adapter records known-issue recall, execution success, and latency. Empty labels have no recall score; unknown findings require manual adjudication. This command explicitly sends dataset code/results to OpenAI and LangSmith; omit `--model` to evaluate static scanning only. Hosted LangSmith evaluation and trace uploads are not run by the tests.

## Docker dashboard

```bash
docker build -t code-review-agent .
docker run --rm -p 127.0.0.1:8501:8501 --env-file .env \
  -v code-review-history:/app/.data code-review-agent
```

The image includes dashboard and scanner extras, excluding Semgrep. Secrets, history, and reports are excluded from the build context. For local Git review, mount a repository read-only under `/repos` and use that path in the UI. The dashboard does not run repository tests.

## Project layout

```text
agents/                   # code_quality, security, test_coverage and shared invocation
integrations/             # GitHub REST and safe ZIP ingestion
app.py                    # Streamlit dashboard
cli.py, main.py            # CLI entrypoints
graph.py                  # Parallel LangGraph workflow and consolidation
citations.py              # Verify/normalize code citations within claimed ranges
repository.py             # Committed Git snapshots
schemas.py, report.py      # Structured results and Markdown output
tools.py, scanners.py      # Built-in and optional scanner adapters
coverage_analysis.py      # Coverage artifact interpretation
coverage_runner.py        # Explicit Docker coverage execution
coverage_worker.py        # Trusted container test entrypoint
history.py                # SQLite review storage
rules/                    # Local Semgrep rules
evals/, tests/            # Seeded regression evaluation and application tests
.github/workflows/        # CI and PR review automation
Dockerfile*               # Dashboard and coverage images
```

See `GAP_ANALYSIS.md` for the comparison against the implementation plan and remaining scope.
