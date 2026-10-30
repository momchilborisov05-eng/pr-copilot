"""Orchestration shared by the webhook, the REST API, the CLI and the GitHub Action."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta

from .config import Settings
from .demo_data import SAMPLES, get_sample
from .github import FakeGitHubClient, GitHubClient, GitHubError, inline_comments, render_markdown, review_event
from .reviewer import BaseReviewer, ReviewError, make_reviewer
from .storage import Store, utcnow

log = logging.getLogger("prcopilot")

REVIEWABLE_ACTIONS = {"opened", "synchronize", "reopened", "ready_for_review"}


@dataclass
class PullRequestJob:
    review_id: int
    repo: str
    number: int
    title: str
    body: str
    head_sha: str | None


class ReviewService:
    def __init__(self, settings: Settings, store: Store, reviewer: BaseReviewer | None = None,
                 github: GitHubClient | None = None):
        self.settings = settings
        self.store = store
        self.reviewer = reviewer or make_reviewer(settings)
        self.github = github or FakeGitHubClient()

    # ---------- entry points ----------

    def review_diff(self, diff: str, *, repo: str = "local/manual", pr_number: int = 0, title: str = "",
                    body: str = "", author: str = "", source: str = "api", post: bool = False) -> dict:
        review_id = self.store.create_review(repo=repo, pr_number=pr_number, title=title, author=author,
                                             source=source, diff=diff)
        self._run(review_id, diff, repo=repo, number=pr_number, title=title, body=body, head_sha=None, post=post)
        return self.store.get_review(review_id)

    def accept_pull_request_event(self, payload: dict) -> PullRequestJob | None:
        """Validate a `pull_request` webhook payload and create a queued review. None = event ignored."""
        if payload.get("action") not in REVIEWABLE_ACTIONS:
            return None
        pr = payload.get("pull_request") or {}
        repo = (payload.get("repository") or {}).get("full_name")
        number = pr.get("number") or payload.get("number")
        if not repo or not number:
            raise ValueError("payload is missing repository.full_name or pull_request.number")
        if pr.get("draft"):
            return None
        title = pr.get("title") or ""
        review_id = self.store.create_review(
            repo=repo, pr_number=int(number), title=title, author=(pr.get("user") or {}).get("login", ""),
            head_sha=(pr.get("head") or {}).get("sha"), source="webhook",
        )
        return PullRequestJob(review_id, repo, int(number), title, pr.get("body") or "", (pr.get("head") or {}).get("sha"))

    def process_job(self, job: PullRequestJob) -> None:
        """Fetch the diff from GitHub, review it and post the result. Runs as a background task."""
        try:
            diff = self.github.get_pr_diff(job.repo, job.number)
        except GitHubError as e:
            self.store.fail_review(job.review_id, str(e))
            return
        self._run(job.review_id, diff, repo=job.repo, number=job.number, title=job.title, body=job.body,
                  head_sha=job.head_sha, post=self.settings.post_to_github, store_diff=True)

    def run_demo(self, sample_id: str | None = None) -> dict:
        """Review one bundled sample PR (rotating through them) as if it came from a webhook."""
        count = self._count()
        sample = get_sample(sample_id) if sample_id else SAMPLES[count % len(SAMPLES)]
        if sample is None:
            raise KeyError(sample_id)
        number = 500 + count
        # Demo reviews are only "posted" to the fake client, never to a real repository.
        return self.review_diff(sample.diff(), repo=sample.repo, pr_number=number, title=sample.title,
                                body=sample.body, author=sample.author, source="demo",
                                post=isinstance(self.github, FakeGitHubClient))

    # ---------- internals ----------

    def _count(self) -> int:
        return self.store.conn.execute("SELECT COUNT(*) FROM reviews").fetchone()[0]

    def _run(self, review_id: int, diff: str, *, repo: str, number: int, title: str, body: str,
             head_sha: str | None, post: bool, store_diff: bool = False) -> None:
        try:
            outcome = self.reviewer.review(diff, title=title, body=body)
        except ReviewError as e:
            log.warning("review %s failed: %s", review_id, e)
            self.store.fail_review(review_id, str(e))
            return
        self.store.complete_review(review_id, outcome, diff=diff if store_diff else None)
        if post and number:
            body_md = render_markdown(outcome.review, model=outcome.model, mode=outcome.mode, cost_usd=outcome.cost_usd)
            try:
                self.github.post_review(repo, number, body=body_md, event=review_event(outcome.review),
                                        comments=inline_comments(outcome.review), commit_id=head_sha)
                self.store.mark_posted(review_id)
            except GitHubError as e:
                log.warning("posting review %s failed: %s", review_id, e)


# ---------- demo history ----------

# The demo history replays the six bundled sample PRs (same diffs, so the canned reviews stay accurate).
DEMO_HISTORY = [
    # (days ago, hour UTC, sample id, PR number)
    (13, 9, "clean-refactor", 212), (13, 15, "missing-tests", 412),
    (12, 11, "n-plus-one", 415),
    (11, 10, "async-blocking", 84), (11, 14, "clean-refactor", 214), (11, 16, "missing-tests", 417),
    (10, 15, "sql-injection", 1031),
    (9, 9, "clean-refactor", 219), (9, 13, "n-plus-one", 1033),
    (8, 10, "missing-tests", 421), (8, 12, "clean-refactor", 88), (8, 17, "async-blocking", 90),
    (7, 11, "leaked-secret", 424),
    (6, 9, "n-plus-one", 426), (6, 14, "clean-refactor", 221),
    (5, 10, "missing-tests", 1038), (5, 13, "clean-refactor", 93), (5, 16, "sql-injection", 430),
    (4, 11, "async-blocking", 95),
    (3, 9, "clean-refactor", 224), (3, 12, "n-plus-one", 436), (3, 15, "missing-tests", 438),
    (2, 10, "leaked-secret", 1041), (2, 14, "clean-refactor", 227),
    (1, 9, "missing-tests", 440), (1, 13, "sql-injection", 1044), (1, 16, "clean-refactor", 98),
    (0, 7, "n-plus-one", 444),
]


def seed_demo_history(service: ReviewService) -> int:
    """Fill an empty database with two weeks of demo reviews so every dashboard view has content."""
    if service._count():
        return 0
    now = utcnow().replace(minute=0, second=0)
    for days, hour, sid, number in DEMO_HISTORY:
        sample = get_sample(sid)
        created = (now - timedelta(days=days)).replace(hour=hour)
        if created > utcnow():
            created = utcnow() - timedelta(minutes=30)
        review_id = service.store.create_review(
            repo=sample.repo, pr_number=number, title=sample.title, author=sample.author,
            source="demo", created_at=created, diff=sample.diff(),
        )
        outcome = service.reviewer.review(sample.diff(), title=sample.title, body=sample.body)
        service.store.complete_review(review_id, outcome, posted=True)
    return len(DEMO_HISTORY)
