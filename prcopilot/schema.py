"""The review contract: the JSON schema Claude must follow and the Pydantic models that re-validate it.

The schema is sent as Structured Outputs (`output_config.format`), so the API guarantees the shape. We still
validate in code: line numbers must point at lines that exist in the diff, unknown files are dropped, and the
overall risk can never be lower than the worst finding.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field, ValidationError, field_validator

__all__ = ["Risk", "Severity", "Category", "Finding", "ChunkReview", "Review", "REVIEW_JSON_SCHEMA",
           "ValidationError", "validate_chunk_review", "merge_reviews", "risk_from_findings"]


class Risk(str, Enum):
    low = "low"
    medium = "medium"
    high = "high"


class Severity(str, Enum):
    info = "info"
    minor = "minor"
    major = "major"
    critical = "critical"


class Category(str, Enum):
    bug = "bug"
    security = "security"
    performance = "performance"
    style = "style"
    tests = "tests"


RISK_ORDER = {Risk.low: 0, Risk.medium: 1, Risk.high: 2}
SEVERITY_ORDER = {Severity.info: 0, Severity.minor: 1, Severity.major: 2, Severity.critical: 3}


class Finding(BaseModel):
    file: str = Field(min_length=1)
    line: int = Field(ge=0, description="New-file line number; 0 means the whole file")
    severity: Severity
    category: Category
    title: str = Field(min_length=3, max_length=140)
    explanation: str = Field(min_length=3)
    suggestion: str = Field(min_length=3)

    @field_validator("file")
    @classmethod
    def _clean_path(cls, v: str) -> str:
        v = v.strip()
        return v[2:] if v.startswith(("a/", "b/")) else v


class ChunkReview(BaseModel):
    """What one model call returns (one chunk of the diff)."""

    summary: str = Field(min_length=3)
    risk: Risk
    findings: list[Finding] = Field(default_factory=list)
    missing_tests: list[str] = Field(default_factory=list)
    changelog: str = ""


class Review(ChunkReview):
    """The merged review of the whole PR."""

    files_reviewed: list[str] = Field(default_factory=list)
    files_skipped: list[str] = Field(default_factory=list)
    chunks: int = 1


_FINDING_PROPS = {
    "file": {"type": "string", "description": "Path exactly as shown in the diff header"},
    "line": {"type": "integer", "description": "New-file line number from the left column; 0 for file-level"},
    "severity": {"type": "string", "enum": [s.value for s in Severity]},
    "category": {"type": "string", "enum": [c.value for c in Category]},
    "title": {"type": "string", "description": "One line, imperative, under 100 characters"},
    "explanation": {"type": "string", "description": "Why it is a problem, grounded in the diff"},
    "suggestion": {"type": "string", "description": "A concrete fix; a short code snippet is welcome"},
}

REVIEW_JSON_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "summary": {"type": "string", "description": "2-4 sentences: what the change does and the main concern"},
        "risk": {"type": "string", "enum": [r.value for r in Risk]},
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": _FINDING_PROPS,
                "required": list(_FINDING_PROPS),
                "additionalProperties": False,
            },
        },
        "missing_tests": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Behaviours added or changed without a test, one short sentence each",
        },
        "changelog": {"type": "string", "description": "One Keep-a-Changelog line, e.g. 'Fixed: ...'"},
    },
    "required": ["summary", "risk", "findings", "missing_tests", "changelog"],
    "additionalProperties": False,
}


def risk_from_findings(findings: list[Finding]) -> Risk:
    worst = max((SEVERITY_ORDER[f.severity] for f in findings), default=0)
    if worst >= 3 or any(f.category == Category.security and SEVERITY_ORDER[f.severity] >= 2 for f in findings):
        return Risk.high
    if worst == 2:
        return Risk.medium
    return Risk.low


def validate_chunk_review(data: dict, valid_lines: dict[str, set[int]] | None = None) -> ChunkReview:
    """Validate model output. With `valid_lines` (path -> new-file line numbers in the diff), findings on
    unknown files are dropped and line numbers that are not in the diff are moved to 0 (file-level)."""
    review = ChunkReview.model_validate(data)
    if valid_lines is not None:
        kept = []
        for f in review.findings:
            if f.file not in valid_lines:
                continue
            if f.line and f.line not in valid_lines[f.file]:
                f = f.model_copy(update={"line": 0})
            kept.append(f)
        review.findings = kept
    floor = risk_from_findings(review.findings)
    if RISK_ORDER[floor] > RISK_ORDER[review.risk]:
        review.risk = floor
    return review


def merge_reviews(parts: list[ChunkReview], files_reviewed: list[str], files_skipped: list[str]) -> Review:
    """Combine chunk reviews: worst risk wins, findings are de-duplicated and sorted by severity."""
    if not parts:
        return Review(summary="Nothing to review: the diff has no reviewable files.", risk=Risk.low,
                      files_reviewed=files_reviewed, files_skipped=files_skipped, chunks=0)
    seen: set[tuple] = set()
    findings: list[Finding] = []
    for p in parts:
        for f in p.findings:
            key = (f.file, f.line, f.category, f.title.lower())
            if key not in seen:
                seen.add(key)
                findings.append(f)
    findings.sort(key=lambda f: (-SEVERITY_ORDER[f.severity], f.file, f.line))
    risk = max((p.risk for p in parts), key=lambda r: RISK_ORDER[r])
    missing = list(dict.fromkeys(m for p in parts for m in p.missing_tests))
    summary = parts[0].summary if len(parts) == 1 else " ".join(p.summary for p in parts)
    changelog = next((p.changelog for p in parts if p.changelog), "")
    return Review(summary=summary, risk=risk, findings=findings, missing_tests=missing, changelog=changelog,
                  files_reviewed=files_reviewed, files_skipped=files_skipped, chunks=len(parts))
