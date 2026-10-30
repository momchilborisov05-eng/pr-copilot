"""GitHub integration: webhook signature check, review rendering and the REST adapter.

`GitHubClient` is the seam. `RestGitHubClient` talks to the real API with httpx; `FakeGitHubClient` records
every call in memory (demo mode and tests), so nothing is ever posted to GitHub by accident.
"""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass, field
from typing import Protocol

import httpx

from .schema import Review

MARKER = "<!-- pr-copilot -->"
RISK_BADGE = {"low": "🟢 Low", "medium": "🟠 Medium", "high": "🔴 High"}
SEVERITY_ICON = {"critical": "🛑", "major": "⚠️", "minor": "🔸", "info": "💬"}


def sign(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def verify_signature(secret: str, body: bytes, header: str | None) -> bool:
    """Constant-time check of GitHub's X-Hub-Signature-256 header. An empty secret never verifies."""
    if not secret or not header or not header.startswith("sha256="):
        return False
    return hmac.compare_digest(sign(secret, body), header.strip())


def render_markdown(review: Review, *, model: str, mode: str, cost_usd: float | None = None,
                    dashboard_url: str | None = None) -> str:
    """The comment posted on the PR (and written to the GitHub Actions job summary)."""
    risk = review.risk.value
    lines = [
        MARKER,
        f"## PR Copilot review · risk {RISK_BADGE[risk]}",
        "",
        review.summary,
        "",
    ]
    if review.findings:
        lines += ["| | Severity | Category | Location | Finding |", "|---|---|---|---|---|"]
        for f in review.findings:
            loc = f"`{f.file}:{f.line}`" if f.line else f"`{f.file}`"
            title = f.title.replace("|", "\\|")
            lines.append(f"| {SEVERITY_ICON[f.severity.value]} | {f.severity.value} | {f.category.value} | {loc} | {title} |")
        lines += ["", "<details><summary>Details and suggested fixes</summary>", ""]
        for i, f in enumerate(review.findings, start=1):
            lines += [f"**{i}. {f.title}** (`{f.file}:{f.line}`)", "", f.explanation, "", "```", f.suggestion, "```", ""]
        lines += ["</details>", ""]
    else:
        lines += ["No findings. ✅", ""]
    if review.missing_tests:
        lines += ["**Missing tests**", ""] + [f"- [ ] {m}" for m in review.missing_tests] + [""]
    if review.changelog:
        lines += ["**Suggested changelog entry**", "", f"> {review.changelog}", ""]
    if review.files_skipped:
        lines += [f"<sub>Skipped (generated, binary or deleted): {', '.join(review.files_skipped[:10])}</sub>", ""]
    footer = f"<sub>{'Demo review (canned, no model call)' if mode == 'demo' else f'Model: {model}'}"
    if cost_usd is not None:
        footer += f" · cost ${cost_usd:.4f}"
    if dashboard_url:
        footer += f" · [dashboard]({dashboard_url})"
    lines.append(footer + " · AI review: verify before acting.</sub>")
    return "\n".join(lines)


def review_event(review: Review, fail_on: str = "never") -> str:
    """GitHub review event. Only REQUEST_CHANGES when the team opted in via fail_on."""
    order = {"low": 0, "medium": 1, "high": 2}
    if fail_on in order and order[review.risk.value] >= order[fail_on]:
        return "REQUEST_CHANGES"
    return "COMMENT"


def inline_comments(review: Review, limit: int = 20) -> list[dict]:
    """Line-level review comments for findings that point at a concrete line."""
    out = []
    for f in review.findings:
        if f.line <= 0 or f.severity.value == "info":
            continue
        out.append({"path": f.file, "line": f.line, "side": "RIGHT",
                    "body": f"{SEVERITY_ICON[f.severity.value]} **{f.title}**\n\n{f.explanation}\n\n**Suggestion:** {f.suggestion}"})
    return out[:limit]


class GitHubError(RuntimeError):
    pass


class GitHubClient(Protocol):
    def get_pr_diff(self, repo: str, number: int) -> str: ...
    def post_review(self, repo: str, number: int, *, body: str, event: str, comments: list[dict],
                    commit_id: str | None = None) -> dict: ...


class RestGitHubClient:
    def __init__(self, token: str, api_url: str = "https://api.github.com", http: httpx.Client | None = None):
        if not token:
            raise GitHubError("GITHUB_TOKEN is required to talk to the GitHub API")
        self.api_url = api_url.rstrip("/")
        self.http = http or httpx.Client(timeout=20)
        self.headers = {
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "pr-copilot",
        }

    def get_pr_diff(self, repo: str, number: int) -> str:
        r = self.http.get(f"{self.api_url}/repos/{repo}/pulls/{number}",
                          headers={**self.headers, "Accept": "application/vnd.github.diff"})
        if r.status_code != 200:
            raise GitHubError(f"Fetching the diff of {repo}#{number} failed: HTTP {r.status_code}")
        return r.text

    def post_review(self, repo, number, *, body, event, comments, commit_id=None):
        payload: dict = {"body": body, "event": event, "comments": comments}
        if commit_id:
            payload["commit_id"] = commit_id
        url = f"{self.api_url}/repos/{repo}/pulls/{number}/reviews"
        r = self.http.post(url, headers={**self.headers, "Accept": "application/vnd.github+json"}, json=payload)
        if r.status_code == 422 and comments:
            # A comment on a line outside the diff makes GitHub reject the whole review: retry without them.
            payload["comments"] = []
            r = self.http.post(url, headers={**self.headers, "Accept": "application/vnd.github+json"}, json=payload)
        if r.status_code not in (200, 201):
            raise GitHubError(f"Posting the review on {repo}#{number} failed: HTTP {r.status_code}")
        return r.json()


@dataclass
class FakeGitHubClient:
    """Records calls instead of making them. `diffs` maps (repo, number) to the diff to return."""

    diffs: dict[tuple[str, int], str] = field(default_factory=dict)
    default_diff: str | None = None
    calls: list[dict] = field(default_factory=list)

    def get_pr_diff(self, repo: str, number: int) -> str:
        self.calls.append({"method": "get_pr_diff", "repo": repo, "number": number})
        diff = self.diffs.get((repo, number), self.default_diff)
        if diff is None:
            from .demo_data import SAMPLES

            diff = SAMPLES[number % len(SAMPLES)].diff()
        return diff

    def post_review(self, repo, number, *, body, event, comments, commit_id=None):
        call = {"method": "post_review", "repo": repo, "number": number, "body": body, "event": event,
                "comments": comments, "commit_id": commit_id}
        self.calls.append(call)
        return {"id": len(self.calls), "state": "COMMENTED" if event == "COMMENT" else event, "fake": True}

    @property
    def posted(self) -> list[dict]:
        return [c for c in self.calls if c["method"] == "post_review"]
