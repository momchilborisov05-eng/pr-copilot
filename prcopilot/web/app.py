"""FastAPI app: GitHub webhook, REST API and the dashboard."""

from __future__ import annotations

import json
import logging
from collections import OrderedDict
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .. import __version__
from ..config import Settings
from ..demo_data import SAMPLES
from ..github import FakeGitHubClient, GitHubClient, RestGitHubClient, verify_signature
from ..prompts import PROMPT_VERSION
from ..reviewer import BaseReviewer
from ..service import ReviewService, seed_demo_history
from ..storage import Store

log = logging.getLogger("prcopilot")
STATIC = Path(__file__).parent / "static"
MAX_DIFF_BYTES = 2_000_000


class ReviewRequest(BaseModel):
    diff: str = Field(min_length=10, max_length=MAX_DIFF_BYTES, description="Unified diff (git diff / GitHub .diff)")
    title: str = Field("", max_length=300)
    body: str = Field("", max_length=10_000)
    repo: str = Field("local/manual", max_length=200, pattern=r"^[\w.-]+/[\w.-]+$")
    pr_number: int = Field(0, ge=0)


class DemoRequest(BaseModel):
    sample_id: str | None = None


def _github_for(settings: Settings) -> GitHubClient:
    if settings.is_demo or not settings.github_token:
        if not settings.is_demo:
            log.warning("GITHUB_TOKEN is not set: reviews will be recorded but not posted to GitHub")
        return FakeGitHubClient()
    return RestGitHubClient(settings.github_token, settings.github_api_url)


def create_app(settings: Settings | None = None, store: Store | None = None, reviewer: BaseReviewer | None = None,
               github: GitHubClient | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    store = store or Store(settings.db_path)
    service = ReviewService(settings, store, reviewer=reviewer, github=github or _github_for(settings))
    seen_deliveries: OrderedDict[str, None] = OrderedDict()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if settings.is_demo and settings.seed_demo_data:
            n = seed_demo_history(service)
            if n:
                log.info("seeded %d demo reviews", n)
        yield

    app = FastAPI(
        title="PR Copilot",
        version=__version__,
        description="AI pull-request reviewer: GitHub webhook, REST API and dashboard.",
        lifespan=lifespan,
    )
    app.state.service = service
    app.state.settings = settings

    # ---------- health ----------

    @app.get("/healthz", tags=["ops"])
    def healthz():
        store.conn.execute("SELECT 1").fetchone()
        return {"status": "ok", "version": __version__, "mode": settings.mode, "model": settings.model}

    @app.get("/api/config", tags=["ops"])
    def config():
        return {
            "mode": settings.mode,
            "model": settings.model,
            "effort": settings.effort,
            "prompt_version": PROMPT_VERSION,
            "posts_to_github": not isinstance(service.github, FakeGitHubClient),
            "webhook_secret_set": bool(settings.webhook_secret),
            "version": __version__,
        }

    # ---------- GitHub webhook ----------

    @app.post("/webhooks/github", status_code=202, tags=["github"])
    async def github_webhook(
        request: Request,
        background: BackgroundTasks,
        x_github_event: str = Header(""),
        x_hub_signature_256: str | None = Header(None),
        x_github_delivery: str | None = Header(None),
    ):
        raw = await request.body()
        if settings.webhook_secret:
            if not verify_signature(settings.webhook_secret, raw, x_hub_signature_256):
                raise HTTPException(401, "invalid or missing X-Hub-Signature-256")
        elif not settings.is_demo:
            raise HTTPException(503, "GITHUB_WEBHOOK_SECRET is not configured")

        if x_github_delivery:
            if x_github_delivery in seen_deliveries:
                return {"status": "duplicate", "delivery": x_github_delivery}
            seen_deliveries[x_github_delivery] = None
            if len(seen_deliveries) > 1000:
                seen_deliveries.popitem(last=False)

        try:
            payload = json.loads(raw or b"{}")
        except json.JSONDecodeError:
            raise HTTPException(400, "body is not valid JSON") from None

        if x_github_event == "ping":
            return JSONResponse({"status": "pong", "zen": payload.get("zen")}, status_code=200)
        if x_github_event != "pull_request":
            return JSONResponse({"status": "ignored", "event": x_github_event}, status_code=200)
        try:
            job = service.accept_pull_request_event(payload)
        except ValueError as e:
            raise HTTPException(422, str(e)) from None
        if job is None:
            return JSONResponse({"status": "ignored", "action": payload.get("action")}, status_code=200)
        background.add_task(service.process_job, job)
        return {"status": "queued", "review_id": job.review_id}

    # ---------- REST API ----------

    @app.get("/api/reviews", tags=["reviews"])
    def list_reviews(
        limit: int = Query(50, ge=1, le=200),
        offset: int = Query(0, ge=0),
        risk: str | None = Query(None, pattern="^(low|medium|high)$"),
        repo: str | None = None,
        status: str | None = Query(None, pattern="^(queued|completed|failed)$"),
    ):
        return {"items": store.list_reviews(limit=limit, offset=offset, risk=risk, repo=repo, status=status)}

    @app.get("/api/reviews/{review_id}", tags=["reviews"])
    def get_review(review_id: int):
        review = store.get_review(review_id)
        if review is None:
            raise HTTPException(404, "review not found")
        return review

    @app.post("/api/reviews", status_code=201, tags=["reviews"])
    def create_review(req: ReviewRequest):
        review = service.review_diff(req.diff, repo=req.repo, pr_number=req.pr_number, title=req.title,
                                     body=req.body, source="api")
        if review["status"] == "failed":
            raise HTTPException(502, review["error"] or "review failed")
        return review

    @app.get("/api/stats", tags=["reviews"])
    def stats(days: int = Query(14, ge=1, le=90)):
        return {**store.stats(days=days), "mode": settings.mode, "model": settings.model}

    @app.get("/api/samples", tags=["demo"])
    def samples():
        return {"items": [{"id": s.id, "title": s.title, "repo": s.repo, "file": s.file} for s in SAMPLES]}

    @app.post("/api/demo/run", status_code=201, tags=["demo"])
    def run_demo(req: DemoRequest | None = None):
        if not settings.is_demo:
            raise HTTPException(409, "demo reviews are only available in demo mode")
        try:
            return service.run_demo(req.sample_id if req else None)
        except KeyError:
            raise HTTPException(404, "unknown sample id") from None

    # ---------- dashboard ----------

    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(STATIC / "index.html")

    return app
