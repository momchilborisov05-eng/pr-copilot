"""Entry point of the reusable GitHub Action (see action.yml).

    python -m prcopilot.action --fail-on high

Inside a `pull_request` workflow it reads the event payload, fetches the PR diff (or uses --diff-file),
runs the review, writes the job summary and step outputs, posts the review on the PR and exits non-zero
when the risk reaches --fail-on. Without ANTHROPIC_API_KEY it runs in demo mode and never calls the API.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .config import Settings
from .github import (
    FakeGitHubClient,
    GitHubClient,
    GitHubError,
    RestGitHubClient,
    inline_comments,
    render_markdown,
    review_event,
)
from .reviewer import BaseReviewer, ReviewError, make_reviewer

RISK_ORDER = {"low": 0, "medium": 1, "high": 2}


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="pr-copilot-action", description="Review a pull request diff in CI.")
    p.add_argument("--diff-file", help="Review this diff file instead of fetching the PR from the GitHub API")
    p.add_argument("--fail-on", default="never", choices=["never", "low", "medium", "high"],
                   help="Exit with code 1 when the review risk is at least this level")
    p.add_argument("--no-post", action="store_true", help="Do not post the review on the pull request")
    p.add_argument("--repo", help="owner/name (default: GITHUB_REPOSITORY)")
    p.add_argument("--pr", type=int, help="PR number (default: from the event payload)")
    p.add_argument("--json-out", help="Also write the full review as JSON to this path")
    return p.parse_args(argv)


def load_event(env: dict[str, str]) -> dict:
    path = env.get("GITHUB_EVENT_PATH")
    if path and Path(path).is_file():
        return json.loads(Path(path).read_text(encoding="utf-8"))
    return {}


def write_output(env: dict[str, str], values: dict[str, str]) -> None:
    path = env.get("GITHUB_OUTPUT")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as fh:
        for k, v in values.items():
            fh.write(f"{k}={v}\n")


def write_summary(env: dict[str, str], markdown: str) -> None:
    path = env.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(markdown + "\n")


def annotations(review) -> list[str]:
    """GitHub workflow commands: findings show up as annotations on the PR's Files tab."""
    level = {"critical": "error", "major": "warning", "minor": "notice", "info": "notice"}
    out = []
    for f in review.findings:
        msg = f"{f.title}: {f.suggestion}".replace("%", "%25").replace("\r", "").replace("\n", "%0A")
        line = f",line={f.line}" if f.line else ""
        out.append(f"::{level[f.severity.value]} file={f.file}{line},title=PR Copilot ({f.category.value})::{msg}")
    return out


def main(argv: list[str] | None = None, env: dict[str, str] | None = None, reviewer: BaseReviewer | None = None,
         github: GitHubClient | None = None, stdout=None) -> int:
    env = dict(os.environ if env is None else env)
    out = stdout or sys.stdout
    args = parse_args(argv)
    settings = Settings.from_env(env)
    event = load_event(env)
    pr = event.get("pull_request") or {}
    repo = args.repo or env.get("GITHUB_REPOSITORY", "")
    number = args.pr or pr.get("number") or 0

    if github is None:
        token = env.get("GITHUB_TOKEN", "")
        github = RestGitHubClient(token, settings.github_api_url) if token else FakeGitHubClient()

    # 1. The diff
    if args.diff_file:
        diff = Path(args.diff_file).read_text(encoding="utf-8")
    elif repo and number:
        try:
            diff = github.get_pr_diff(repo, int(number))
        except GitHubError as e:
            print(f"::error title=PR Copilot::{e}", file=out)
            return 2
    else:
        print("::error title=PR Copilot::No diff: pass --diff-file or run on a pull_request event", file=out)
        return 2

    # 2. The review
    reviewer = reviewer or make_reviewer(settings)
    try:
        outcome = reviewer.review(diff, title=pr.get("title", ""), body=pr.get("body") or "")
    except ReviewError as e:
        print(f"::error title=PR Copilot::{e}", file=out)
        return 2
    review = outcome.review

    # 3. Report: annotations, job summary, step outputs
    markdown = render_markdown(review, model=outcome.model, mode=outcome.mode, cost_usd=outcome.cost_usd)
    for line in annotations(review):
        print(line, file=out)
    write_summary(env, markdown)
    serious = sum(1 for f in review.findings if f.severity.value in ("critical", "major"))
    write_output(env, {
        "risk": review.risk.value,
        "findings": str(len(review.findings)),
        "serious-findings": str(serious),
        "cost-usd": f"{outcome.cost_usd:.6f}",
        "mode": outcome.mode,
    })
    if args.json_out:
        Path(args.json_out).write_text(json.dumps({
            "review": review.model_dump(mode="json"), "usage": outcome.usage.to_dict(),
            "cost_usd": outcome.cost_usd, "model": outcome.model, "mode": outcome.mode,
        }, indent=2), encoding="utf-8")
    print(f"PR Copilot: risk={review.risk.value} findings={len(review.findings)} mode={outcome.mode} "
          f"cost=${outcome.cost_usd:.4f}", file=out)

    # 4. Post on the PR (fork PRs get a read-only token: warn, don't fail the build)
    if not args.no_post and repo and number:
        try:
            github.post_review(repo, int(number), body=markdown, event=review_event(review, args.fail_on),
                               comments=inline_comments(review), commit_id=(pr.get("head") or {}).get("sha"))
        except GitHubError as e:
            print(f"::warning title=PR Copilot::Could not post the review: {e}", file=out)

    # 5. Gate
    if args.fail_on != "never" and RISK_ORDER[review.risk.value] >= RISK_ORDER[args.fail_on]:
        print(f"::error title=PR Copilot::Risk {review.risk.value} reaches the fail-on threshold ({args.fail_on})", file=out)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
