"""The review pipeline: parse -> filter -> chunk -> review each chunk -> validate -> merge.

Two implementations share the pipeline:
- ClaudeReviewer: one Claude call per chunk, Structured Outputs for the JSON, prompt-cached system prompt.
- DemoReviewer:   no network. Canned reviews for the bundled samples, static-check findings for anything else,
                  estimated token counts. Used whenever ANTHROPIC_API_KEY is missing, and in every test.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any

from .config import Settings
from .demo_data import find_sample
from .diffparse import Chunk, FileDiff, chunk_files, diff_stats, filter_files, missing_test_candidates, parse_diff
from .heuristics import hints, scan
from .pricing import Usage, cost_usd, cost_without_cache, estimate_tokens
from .prompts import PROMPT_VERSION, SYSTEM_PROMPT, user_prompt
from .schema import (
    REVIEW_JSON_SCHEMA,
    SEVERITY_ORDER,
    ChunkReview,
    Finding,
    Review,
    ValidationError,
    merge_reviews,
    risk_from_findings,
    validate_chunk_review,
)

FALLBACK_MODELS = {"claude-opus-5-5", "claude-opus-5", "claude-fable-5-1", "claude-sonnet-5-5"}


class ReviewError(RuntimeError):
    """The model could not produce a usable review (refusal, truncation, invalid JSON, API error)."""


@dataclass
class ReviewOutcome:
    review: Review
    usage: Usage
    model: str
    mode: str                       # "live" | "demo"
    duration_ms: int
    estimated: bool                 # True when tokens/latency are estimates (demo mode)
    stats: dict = field(default_factory=dict)
    prompt_version: str = PROMPT_VERSION

    @property
    def cost_usd(self) -> float:
        return cost_usd(self.usage, self.model)

    @property
    def cost_without_cache_usd(self) -> float:
        return cost_without_cache(self.usage, self.model)


@dataclass
class PreparedDiff:
    files: list[FileDiff]
    skipped: list[str]
    chunks: list[Chunk]
    hints: list[str]
    valid_lines: dict[str, set[int]]
    stats: dict


def prepare(diff_text: str, settings: Settings) -> PreparedDiff:
    all_files = parse_diff(diff_text)
    files, skipped = filter_files(all_files, settings.ignored_paths)
    if len(files) > settings.max_files:
        skipped += [f.path for f in files[settings.max_files:]]
        files = files[: settings.max_files]
    chunks = chunk_files(files, settings.max_chunk_chars) if files else []
    return PreparedDiff(
        files=files,
        skipped=skipped,
        chunks=chunks,
        hints=hints(files),
        valid_lines={f.path: f.changed_new_lines() for f in files},
        stats=diff_stats(files),
    )


class BaseReviewer:
    mode = "base"
    estimated = False

    def __init__(self, settings: Settings):
        self.settings = settings
        self.model = settings.model

    def review(self, diff_text: str, *, title: str = "", body: str = "") -> ReviewOutcome:
        started = time.perf_counter()
        prep = prepare(diff_text, self.settings)
        parts: list[ChunkReview] = []
        usage = Usage()
        for i, chunk in enumerate(prep.chunks, start=1):
            part, u = self.review_chunk(chunk, prep, title=title, body=body, part=i, parts=len(prep.chunks))
            parts.append(part)
            usage = usage + u
        review = merge_reviews(parts, [f.path for f in prep.files], prep.skipped)
        return ReviewOutcome(
            review=review, usage=usage, model=self.model, mode=self.mode,
            duration_ms=self.duration_ms(started, prep), estimated=self.estimated, stats=prep.stats,
        )

    def duration_ms(self, started: float, prep: PreparedDiff) -> int:
        return int((time.perf_counter() - started) * 1000)

    def review_chunk(self, chunk: Chunk, prep: PreparedDiff, *, title: str, body: str, part: int,
                     parts: int) -> tuple[ChunkReview, Usage]:
        raise NotImplementedError


class ClaudeReviewer(BaseReviewer):
    mode = "live"

    def __init__(self, settings: Settings, client: Any = None):
        super().__init__(settings)
        if client is None:
            import anthropic

            client = anthropic.Anthropic(max_retries=3)
        self.client = client

    def request_kwargs(self, prompt: str) -> dict:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": 16000,
            # Stable prefix -> cached. Per-PR content lives in the user message after the breakpoint.
            "system": [{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}],
            "messages": [{"role": "user", "content": prompt}],
            # Structured Outputs: the API guarantees JSON matching the schema. (Forced tool_choice is not
            # accepted by Claude Opus 5.5, so a JSON-schema output format is the right tool here.)
            "output_config": {
                "effort": self.settings.effort,
                "format": {"type": "json_schema", "schema": REVIEW_JSON_SCHEMA},
            },
        }
        if self.model in FALLBACK_MODELS:
            # Server-side fallback: if a safety classifier declines, the API retries on a fallback model.
            kwargs["betas"] = ["server-side-fallback-2026-07-01"]
            kwargs["fallbacks"] = "default"
        return kwargs

    def review_chunk(self, chunk, prep, *, title, body, part, parts):
        prompt = user_prompt(title=title, body=body, chunk_text=chunk.render(), part=part, parts=parts,
                             heuristics=[h for h in prep.hints if h.split(":", 1)[0] in chunk.paths])
        try:
            response = self.client.beta.messages.create(**self.request_kwargs(prompt))
        except Exception as e:  # SDK errors are re-raised with a readable message
            raise ReviewError(f"Claude API call failed: {e}") from e

        if response.stop_reason == "refusal":
            raise ReviewError("The model declined to review this diff (stop_reason=refusal).")
        if response.stop_reason == "max_tokens":
            raise ReviewError("The review was cut off (max_tokens). Lower PRCOPILOT_MAX_CHUNK_CHARS.")
        text = next((b.text for b in response.content if getattr(b, "type", "") == "text"), "")
        try:
            review = validate_chunk_review(json.loads(text), prep.valid_lines)
        except (json.JSONDecodeError, ValidationError) as e:
            raise ReviewError(f"The model returned an invalid review: {e}") from e

        u = response.usage
        usage = Usage(
            input_tokens=getattr(u, "input_tokens", 0) or 0,
            output_tokens=getattr(u, "output_tokens", 0) or 0,
            cache_read_tokens=getattr(u, "cache_read_input_tokens", 0) or 0,
            cache_write_tokens=getattr(u, "cache_creation_input_tokens", 0) or 0,
            calls=1,
        )
        if getattr(response, "model", None):
            self.model = response.model
        return review, usage


class DemoReviewer(BaseReviewer):
    """Deterministic stand-in for Claude. Clearly labelled as demo output wherever it is shown."""

    mode = "demo"
    estimated = True

    def __init__(self, settings: Settings):
        super().__init__(settings)
        self._cache_warm = False
        self.calls: list[dict] = []

    def review(self, diff_text: str, *, title: str = "", body: str = "") -> ReviewOutcome:
        sample = find_sample(diff_text)
        if sample is None:
            return super().review(diff_text, title=title, body=body)
        prep = prepare(diff_text, self.settings)
        canned = validate_chunk_review(sample.review, prep.valid_lines)
        review = merge_reviews([canned], [f.path for f in prep.files], prep.skipped)
        usage = Usage()
        for i, chunk in enumerate(prep.chunks, start=1):
            usage = usage + self._estimate(chunk, prep, title, body, i, len(prep.chunks), canned)
        return ReviewOutcome(review=review, usage=usage, model=self.model, mode=self.mode,
                             duration_ms=self.duration_ms(0.0, prep), estimated=True, stats=prep.stats)

    def review_chunk(self, chunk, prep, *, title, body, part, parts):
        paths = set(chunk.paths)
        findings = [
            Finding(file=h.file, line=h.line, severity=h.rule.severity, category=h.rule.category,
                    title=h.rule.title, explanation=h.rule.explanation, suggestion=h.rule.suggestion)
            for h in scan([f for f, _ in chunk.parts])
        ]
        missing = [p for p in missing_test_candidates(prep.files) if p in paths]
        for p in missing:
            findings.append(Finding(file=p, line=0, severity="minor", category="tests",
                                    title="New code without tests",
                                    explanation="This file gains new logic and the PR changes no test file.",
                                    suggestion="Add unit tests for the new behaviour."))
        risk = risk_from_findings(findings)
        names = ", ".join(sorted(paths))
        if findings:
            worst = max(findings, key=lambda f: SEVERITY_ORDER[f.severity])
            summary = (f"[demo] Static review of {names}: {len(findings)} finding(s); most important: "
                       f"{worst.title.lower()} ({worst.file}:{worst.line}).")
        else:
            summary = f"[demo] Static review of {names}: no issues found by the rule set."
        review = ChunkReview(summary=summary, risk=risk, findings=findings,
                             missing_tests=[f"{p}: add tests for the new code" for p in missing],
                             changelog=f"Changed: {title}" if title else "")
        review = validate_chunk_review(review.model_dump(mode="json"), prep.valid_lines)
        return review, self._estimate(chunk, prep, title, body, part, parts, review)

    def _estimate(self, chunk, prep, title, body, part, parts, review: ChunkReview) -> Usage:
        prompt = user_prompt(title=title, body=body, chunk_text=chunk.render(), part=part, parts=parts,
                             heuristics=prep.hints)
        system = estimate_tokens(SYSTEM_PROMPT) + estimate_tokens(json.dumps(REVIEW_JSON_SCHEMA))
        usage = Usage(
            input_tokens=estimate_tokens(prompt),
            output_tokens=estimate_tokens(review.model_dump_json()) + 180,
            cache_read_tokens=system if self._cache_warm else 0,
            cache_write_tokens=0 if self._cache_warm else system,
            calls=1,
        )
        self._cache_warm = True
        self.calls.append({"part": part, "chars": len(chunk)})
        return usage

    def duration_ms(self, started: float, prep: PreparedDiff) -> int:
        # Simulated, deterministic latency so the dashboard has plausible numbers. Never presented as measured.
        changed = prep.stats.get("additions", 0) + prep.stats.get("deletions", 0)
        return 2600 + 38 * changed + 1900 * max(0, len(prep.chunks) - 1)


def make_reviewer(settings: Settings, client: Any = None) -> BaseReviewer:
    if settings.is_demo:
        return DemoReviewer(settings)
    return ClaudeReviewer(settings, client=client)
