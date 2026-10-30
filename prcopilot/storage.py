"""SQLite persistence (stdlib sqlite3). One row per review, one row per finding, so stats are plain SQL."""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import UTC, datetime, timedelta

SCHEMA = """
CREATE TABLE IF NOT EXISTS reviews (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    repo            TEXT NOT NULL,
    pr_number       INTEGER NOT NULL,
    title           TEXT NOT NULL DEFAULT '',
    author          TEXT NOT NULL DEFAULT '',
    head_sha        TEXT,
    source          TEXT NOT NULL,              -- webhook | api | demo | action
    status          TEXT NOT NULL,              -- queued | completed | failed
    error           TEXT,
    risk            TEXT,
    summary         TEXT,
    changelog       TEXT,
    missing_tests   TEXT NOT NULL DEFAULT '[]', -- JSON list
    files_reviewed  TEXT NOT NULL DEFAULT '[]',
    files_skipped   TEXT NOT NULL DEFAULT '[]',
    additions       INTEGER NOT NULL DEFAULT 0,
    deletions       INTEGER NOT NULL DEFAULT 0,
    chunks          INTEGER NOT NULL DEFAULT 0,
    model           TEXT,
    mode            TEXT,
    estimated       INTEGER NOT NULL DEFAULT 0,
    prompt_version  TEXT,
    input_tokens    INTEGER NOT NULL DEFAULT 0,
    output_tokens   INTEGER NOT NULL DEFAULT 0,
    cache_read_tokens  INTEGER NOT NULL DEFAULT 0,
    cache_write_tokens INTEGER NOT NULL DEFAULT 0,
    model_calls     INTEGER NOT NULL DEFAULT 0,
    cost_usd        REAL NOT NULL DEFAULT 0,
    cost_without_cache_usd REAL NOT NULL DEFAULT 0,
    duration_ms     INTEGER,
    posted          INTEGER NOT NULL DEFAULT 0,
    diff            TEXT NOT NULL DEFAULT '',
    created_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_reviews_created ON reviews(created_at);
CREATE INDEX IF NOT EXISTS idx_reviews_repo_pr ON reviews(repo, pr_number);

CREATE TABLE IF NOT EXISTS findings (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    review_id   INTEGER NOT NULL REFERENCES reviews(id) ON DELETE CASCADE,
    file        TEXT NOT NULL,
    line        INTEGER NOT NULL,
    severity    TEXT NOT NULL,
    category    TEXT NOT NULL,
    title       TEXT NOT NULL,
    explanation TEXT NOT NULL,
    suggestion  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_findings_review ON findings(review_id);
"""

SEVERITY_SQL = "CASE severity WHEN 'critical' THEN 0 WHEN 'major' THEN 1 WHEN 'minor' THEN 2 ELSE 3 END"


def utcnow() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


class Store:
    def __init__(self, path: str = ":memory:"):
        self.path = path
        self._lock = threading.RLock()
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        if path != ":memory:":
            self.conn.execute("PRAGMA journal_mode = WAL")
        self.conn.executescript(SCHEMA)

    def close(self) -> None:
        self.conn.close()

    # ---------- writes ----------

    def create_review(self, *, repo: str, pr_number: int, title: str = "", author: str = "", head_sha: str | None = None,
                      source: str = "api", diff: str = "", created_at: datetime | None = None) -> int:
        with self._lock, self.conn:
            cur = self.conn.execute(
                "INSERT INTO reviews (repo, pr_number, title, author, head_sha, source, status, diff, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, 'queued', ?, ?)",
                (repo, pr_number, title, author, head_sha, source, diff, (created_at or utcnow()).isoformat()),
            )
            return int(cur.lastrowid)

    def complete_review(self, review_id: int, outcome, *, posted: bool = False, diff: str | None = None) -> None:
        r, u = outcome.review, outcome.usage
        with self._lock, self.conn:
            self.conn.execute(
                """UPDATE reviews SET status='completed', error=NULL, risk=?, summary=?, changelog=?, missing_tests=?,
                   files_reviewed=?, files_skipped=?, additions=?, deletions=?, chunks=?, model=?, mode=?, estimated=?,
                   prompt_version=?, input_tokens=?, output_tokens=?, cache_read_tokens=?, cache_write_tokens=?,
                   model_calls=?, cost_usd=?, cost_without_cache_usd=?, duration_ms=?, posted=?,
                   diff=COALESCE(?, diff) WHERE id=?""",
                (r.risk.value, r.summary, r.changelog, json.dumps(r.missing_tests), json.dumps(r.files_reviewed),
                 json.dumps(r.files_skipped), outcome.stats.get("additions", 0), outcome.stats.get("deletions", 0),
                 r.chunks, outcome.model, outcome.mode, int(outcome.estimated), outcome.prompt_version,
                 u.input_tokens, u.output_tokens, u.cache_read_tokens, u.cache_write_tokens, u.calls,
                 outcome.cost_usd, outcome.cost_without_cache_usd, outcome.duration_ms, int(posted), diff, review_id),
            )
            self.conn.execute("DELETE FROM findings WHERE review_id=?", (review_id,))
            self.conn.executemany(
                "INSERT INTO findings (review_id, file, line, severity, category, title, explanation, suggestion)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [(review_id, f.file, f.line, f.severity.value, f.category.value, f.title, f.explanation, f.suggestion)
                 for f in r.findings],
            )

    def fail_review(self, review_id: int, error: str) -> None:
        with self._lock, self.conn:
            self.conn.execute("UPDATE reviews SET status='failed', error=? WHERE id=?", (error[:1000], review_id))

    def mark_posted(self, review_id: int) -> None:
        with self._lock, self.conn:
            self.conn.execute("UPDATE reviews SET posted=1 WHERE id=?", (review_id,))

    # ---------- reads ----------

    def get_review(self, review_id: int, include_diff: bool = True) -> dict | None:
        with self._lock:
            row = self.conn.execute("SELECT * FROM reviews WHERE id=?", (review_id,)).fetchone()
            if row is None:
                return None
            findings = self.conn.execute(
                f"SELECT file, line, severity, category, title, explanation, suggestion FROM findings"
                f" WHERE review_id=? ORDER BY {SEVERITY_SQL}, file, line", (review_id,)).fetchall()
        out = _row(row, include_diff)
        out["findings"] = [dict(f) for f in findings]
        return out

    def list_reviews(self, *, limit: int = 50, offset: int = 0, risk: str | None = None, repo: str | None = None,
                     status: str | None = None) -> list[dict]:
        where, args = [], []
        for col, val in (("r.risk", risk), ("r.repo", repo), ("r.status", status)):
            if val:
                where.append(f"{col} = ?")
                args.append(val)
        sql = f"""
            SELECT r.*, COUNT(f.id) AS findings_count,
                   SUM(CASE WHEN f.severity IN ('critical','major') THEN 1 ELSE 0 END) AS serious_count
            FROM reviews r LEFT JOIN findings f ON f.review_id = r.id
            {'WHERE ' + ' AND '.join(where) if where else ''}
            GROUP BY r.id ORDER BY r.created_at DESC, r.id DESC LIMIT ? OFFSET ?"""
        with self._lock:
            rows = self.conn.execute(sql, (*args, limit, offset)).fetchall()
        out = []
        for row in rows:
            d = _row(row, include_diff=False)
            d["findings_count"] = row["findings_count"] or 0
            d["serious_count"] = row["serious_count"] or 0
            out.append(d)
        return out

    def stats(self, days: int = 14) -> dict:
        since = (utcnow() - timedelta(days=days - 1)).date().isoformat()
        with self._lock:
            k = self.conn.execute("""
                SELECT COUNT(*) AS reviews,
                       SUM(risk = 'high') AS high_risk,
                       SUM(risk = 'medium') AS medium_risk,
                       SUM(risk = 'low') AS low_risk,
                       AVG(duration_ms) AS avg_ms,
                       COALESCE(SUM(cost_usd), 0) AS cost,
                       COALESCE(SUM(cost_without_cache_usd), 0) AS cost_no_cache,
                       COALESCE(SUM(input_tokens + cache_read_tokens + cache_write_tokens), 0) AS prompt_tokens,
                       COALESCE(SUM(cache_read_tokens), 0) AS cache_read,
                       COALESCE(SUM(output_tokens), 0) AS output_tokens,
                       COUNT(DISTINCT repo) AS repos,
                       MAX(estimated) AS any_estimated
                FROM reviews WHERE status = 'completed'""").fetchone()
            failed = self.conn.execute("SELECT COUNT(*) FROM reviews WHERE status='failed'").fetchone()[0]
            trend = self.conn.execute("""
                SELECT substr(created_at, 1, 10) AS day,
                       SUM(risk = 'low') AS low, SUM(risk = 'medium') AS medium, SUM(risk = 'high') AS high
                FROM reviews WHERE status = 'completed' AND substr(created_at, 1, 10) >= ?
                GROUP BY day ORDER BY day""", (since,)).fetchall()
            cats = self.conn.execute("""
                SELECT f.category, f.severity, COUNT(*) AS n FROM findings f
                JOIN reviews r ON r.id = f.review_id WHERE r.status = 'completed'
                GROUP BY f.category, f.severity""").fetchall()
            files = self.conn.execute("""
                SELECT f.file, COUNT(*) AS n, SUM(f.severity IN ('critical','major')) AS serious
                FROM findings f GROUP BY f.file ORDER BY serious DESC, n DESC LIMIT 5""").fetchall()
        by_day = {r["day"]: dict(r) for r in trend}
        start = utcnow().date() - timedelta(days=days - 1)
        series = []
        for i in range(days):
            d = (start + timedelta(days=i)).isoformat()
            row = by_day.get(d, {})
            series.append({"day": d, "low": row.get("low") or 0, "medium": row.get("medium") or 0,
                           "high": row.get("high") or 0})
        categories: dict[str, dict] = {}
        for c in ("security", "bug", "performance", "tests", "style"):
            categories[c] = {"total": 0, "critical": 0, "major": 0, "minor": 0, "info": 0}
        for r in cats:
            cat = categories.setdefault(r["category"], {"total": 0, "critical": 0, "major": 0, "minor": 0, "info": 0})
            cat[r["severity"]] = r["n"]
            cat["total"] += r["n"]
        reviews = k["reviews"] or 0
        prompt_tokens = k["prompt_tokens"] or 0
        return {
            "reviews": reviews,
            "failed": failed,
            "repos": k["repos"] or 0,
            "high_risk": k["high_risk"] or 0,
            "medium_risk": k["medium_risk"] or 0,
            "low_risk": k["low_risk"] or 0,
            "avg_review_ms": int(k["avg_ms"] or 0),
            "total_cost_usd": round(k["cost"] or 0, 4),
            "avg_cost_usd": round((k["cost"] or 0) / reviews, 4) if reviews else 0.0,
            "cost_without_cache_usd": round(k["cost_no_cache"] or 0, 4),
            "cache_hit_rate": round((k["cache_read"] or 0) / prompt_tokens, 3) if prompt_tokens else 0.0,
            "output_tokens": k["output_tokens"] or 0,
            "estimated": bool(k["any_estimated"]),
            "trend": series,
            "categories": categories,
            "hotspots": [dict(r) for r in files],
        }


def _row(row: sqlite3.Row, include_diff: bool) -> dict:
    d = dict(row)
    for key in ("missing_tests", "files_reviewed", "files_skipped"):
        d[key] = json.loads(d.get(key) or "[]")
    d["estimated"] = bool(d.get("estimated"))
    d["posted"] = bool(d.get("posted"))
    if not include_diff:
        d.pop("diff", None)
    return d
