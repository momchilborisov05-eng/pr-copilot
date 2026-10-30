# CLAUDE.md

Guidance for Claude Code (and other AI coding assistants) working in this repository. The full workflow, prompt
patterns and guardrails are in [docs/ai-sdlc.md](docs/ai-sdlc.md); this file is the short version the assistant
reads on every session.

## What this is

PR Copilot is a FastAPI service and a GitHub Action that review pull-request diffs with Claude and post the result
back to GitHub. Python 3.11+, stdlib `sqlite3`, Pydantic v2, `httpx`, the Anthropic SDK. No ORM, no frontend build.

## Commands

```bash
python -m unittest discover -s tests          # all tests (offline, ~1 s). Run after every change.
python -m prcopilot serve --port 8000         # API + dashboard, demo mode without ANTHROPIC_API_KEY
python -m prcopilot review examples/diffs/sql-injection-flask.diff
python -m prcopilot.action --diff-file examples/diffs/clean-refactor.diff --no-post
ruff check .                                  # lint (config in pyproject.toml)
```

## Map

| Path | Responsibility |
|---|---|
| `prcopilot/diffparse.py` | unified diff -> files, hunks, new-file line numbers; filtering; chunking |
| `prcopilot/schema.py` | the review contract (JSON schema + Pydantic), grounding, merge |
| `prcopilot/prompts.py` | system prompt and rubric (prompt-cached; bump `PROMPT_VERSION` when it changes) |
| `prcopilot/reviewer.py` | `ClaudeReviewer` (live) and `DemoReviewer` (offline) behind one pipeline |
| `prcopilot/heuristics.py` | regex static checks: hints for Claude, findings in demo mode |
| `prcopilot/github.py` | HMAC check, Markdown rendering, REST + fake GitHub clients |
| `prcopilot/service.py` | orchestration for webhook, API, demo; demo history seed |
| `prcopilot/storage.py` | SQLite schema and queries (reviews, findings, stats) |
| `prcopilot/action.py` | GitHub Action entry point (`action.yml` calls it) |
| `prcopilot/web/` | FastAPI app and the static dashboard (vanilla JS, Chart.js, GSAP) |

## Rules

1. **Never call the real Claude or GitHub API in tests or during development.** Use `DemoReviewer`,
   `FakeAnthropic` (tests/helpers.py) and `FakeGitHubClient`. Live calls only happen when a human sets
   `ANTHROPIC_API_KEY` / `GITHUB_TOKEN`.
2. **Never read or print `.env`** or any secret. Configuration goes through `Settings.from_env()`.
3. **Tests first for behaviour changes.** Every bug fix gets a failing test before the fix. Use `unittest`
   (pytest is not a dependency).
4. **Keep the review contract stable.** Changing `REVIEW_JSON_SCHEMA` means updating the Pydantic models, the
   canned reviews in `demo_data.py`, the dashboard and `render_markdown`, plus tests. Bump `PROMPT_VERSION` when
   the prompt or rubric changes.
5. **The system prompt must stay byte-identical across requests** (no dates, repo names or IDs) so prompt
   caching keeps working. Per-PR data goes in the user message.
6. **Model output is untrusted.** Validate it (`validate_chunk_review`), never execute it, never let a finding
   point at a line that is not in the diff.
7. Small, reviewable diffs. Don't add dependencies without saying why; prefer the standard library.
8. Default model is `claude-opus-5-5`; don't hard-code another model, read `Settings.model`.

## Definition of done

- `python -m unittest discover -s tests` passes and new behaviour has tests.
- `ruff check .` is clean.
- README / docs updated if a command, env var, endpoint or the review schema changed.
- The dashboard still loads in demo mode (`python -m prcopilot serve`) in dark and light theme.
