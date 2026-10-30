"""Cheap static checks over the added lines of a diff.

They run before every review. In live mode their hits are passed to Claude as hints to verify (not as truth).
In demo mode they turn any diff that is not one of the bundled samples into a deterministic review, so the
dashboard and the GitHub Action work end to end without an API key.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .diffparse import FileDiff, missing_test_candidates


@dataclass(frozen=True)
class Rule:
    id: str
    pattern: re.Pattern
    severity: str
    category: str
    title: str
    explanation: str
    suggestion: str
    languages: tuple[str, ...] = ()        # empty = any language


@dataclass(frozen=True)
class Hit:
    rule: Rule
    file: str
    line: int
    code: str

    def as_hint(self) -> str:
        return f"{self.file}:{self.line} {self.rule.id} - {self.rule.title}"


RULES: list[Rule] = [
    Rule("aws-access-key", re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "critical", "security",
         "AWS access key committed to the repository",
         "An AWS access key id is hard-coded. Anyone with read access to the repo (or its history) can use it.",
         "Revoke the key in IAM now, remove it from the code and load it from the environment or a secrets manager."),
    Rule("private-key", re.compile(r"-----BEGIN (RSA |EC |OPENSSH |)PRIVATE KEY-----"), "critical", "security",
         "Private key committed to the repository",
         "A private key is part of the diff and will stay in git history even if the file is deleted later.",
         "Rotate the key pair, delete the file and add the path to .gitignore."),
    Rule("token-literal", re.compile(r"\b(ghp_[A-Za-z0-9]{20,}|xox[bap]-[A-Za-z0-9-]{10,}|sk-ant-[A-Za-z0-9-]{10,})"),
         "critical", "security", "API token committed to the repository",
         "A provider token is hard-coded in the change.",
         "Revoke the token and read it from an environment variable instead."),
    Rule("hardcoded-secret", re.compile(
        r"""(?i)\b\w*(password|passwd|secret|api_?key|token)\w*\s*[:=]\s*["'][^"'\s]{8,}["']"""),
         "critical", "security", "Hard-coded secret",
         "A credential-looking value is assigned as a string literal.",
         "Load it with os.environ[...] (fail fast if missing) and keep real values out of git."),
    Rule("credentials-in-url", re.compile(r"[a-z]+://[^/\s:@\"']+:[^/\s@\"']+@"), "critical", "security",
         "Database credentials in a connection URL",
         "The default connection string contains a username and password, so the fallback leaks a real credential.",
         "Remove the default and require DATABASE_URL from the environment."),
    Rule("sql-fstring", re.compile(r"""(?i)f["'].*\b(select|insert|update|delete)\b.*\{|\b(select|where|order by)\b[^"']*\{\w+\}"""),
         "critical", "security", "SQL built with string formatting",
         "User-controlled values are interpolated into SQL, which allows SQL injection.",
         "Use bound parameters (`?` / `%s` placeholders) and whitelist identifiers such as ORDER BY columns.",
         ("python",)),
    Rule("debug-true", re.compile(r"^\s*DEBUG\s*=\s*True\b"), "major", "security", "DEBUG hard-coded to True",
         "Debug mode exposes stack traces and settings and must never depend on someone remembering to flip it.",
         "Read DEBUG from the environment with a safe default of False.", ("python",)),
    Rule("cors-all", re.compile(r"(?i)CORS_ALLOW_ALL_ORIGINS\s*=\s*True|allow_origins\s*=\s*\[\s*[\"']\*"), "major",
         "security", "CORS open to every origin", "Any website can call this API from a user's browser.",
         "List the allowed origins explicitly per environment."),
    Rule("requests-in-async", re.compile(r"\brequests\.(get|post|put|delete|patch)\("), "major", "performance",
         "Blocking HTTP call",
         "`requests` blocks the thread; inside an `async def` endpoint it stalls the whole event loop.",
         "Use `httpx.AsyncClient` with `await`, or move the call to a thread with `run_in_threadpool`.", ("python",)),
    Rule("no-timeout", re.compile(r"\brequests\.(get|post|put|delete|patch)\((?!.*timeout=)"), "minor", "bug",
         "HTTP call without a timeout",
         "Without a timeout a slow upstream can hang the request forever.",
         "Pass an explicit `timeout=` (for example 5 seconds).", ("python",)),
    Rule("bare-except", re.compile(r"^\s*except(\s+Exception)?\s*:\s*$"), "minor", "bug", "Broad exception handler",
         "Catching everything hides real failures and makes incidents hard to debug.",
         "Catch the specific exceptions you expect and log the rest.", ("python",)),
    Rule("eval-exec", re.compile(r"(?<![\w.])(eval|exec)\("), "critical", "security", "eval/exec on dynamic input",
         "eval/exec can execute arbitrary code if any part of the input is user-controlled.",
         "Use ast.literal_eval or an explicit parser.", ("python",)),
    Rule("yaml-load", re.compile(r"\byaml\.load\((?!.*Loader=yaml\.SafeLoader)"), "major", "security",
         "yaml.load without SafeLoader", "yaml.load can construct arbitrary Python objects.",
         "Use yaml.safe_load.", ("python",)),
    Rule("shell-true", re.compile(r"subprocess\.\w+\(.*shell\s*=\s*True"), "major", "security",
         "subprocess with shell=True", "Shell interpolation of variables allows command injection.",
         "Pass the command as a list and keep shell=False.", ("python",)),
    Rule("print-debug", re.compile(r"^\s*(print\(|breakpoint\(\)|import pdb)"), "info", "style",
         "Debug statement left in", "Debug output in application code is noise in production logs.",
         "Remove it or use the logging module.", ("python",)),
]

_LOOP = re.compile(r"^\s*for\s+\w+\s+in\s+")
_QUERY_IN_LOOP = re.compile(r"session\.(get|scalars?|execute|query)\(|\.objects\.(get|filter)\(|\.query\.")


def scan(files: list[FileDiff]) -> list[Hit]:
    hits: list[Hit] = []
    for f in files:
        if f.is_test or f.path.endswith(".example"):
            continue
        added = f.added_lines()
        for ln in added:
            for rule in RULES:
                if rule.languages and f.language not in rule.languages:
                    continue
                if rule.pattern.search(ln.text):
                    hits.append(Hit(rule, f.path, ln.new_no or 0, ln.text.strip()))
        hits.extend(_n_plus_one(f))
    return _dedupe(hits)


def _n_plus_one(f: FileDiff) -> list[Hit]:
    """A query call within the 4 added lines after a `for` loop header: the classic N+1 shape."""
    out = []
    added = f.added_lines()
    for i, ln in enumerate(added):
        if not _LOOP.match(ln.text):
            continue
        for nxt in added[i + 1:i + 5]:
            if _QUERY_IN_LOOP.search(nxt.text):
                out.append(Hit(N_PLUS_ONE, f.path, nxt.new_no or 0, nxt.text.strip()))
                break
    return out


N_PLUS_ONE = Rule("n-plus-one", _QUERY_IN_LOOP, "major", "performance", "Query inside a loop (N+1)",
                  "A database query runs once per loop iteration, so the page does N+1 round trips.",
                  "Load the related rows in one query (join / selectinload / IN (...)) before the loop.")


def _dedupe(hits: list[Hit]) -> list[Hit]:
    # One line can match several rules; keep the most specific (first in RULES order) per line and category.
    seen: set[tuple] = set()
    out = []
    for h in hits:
        key = (h.file, h.line, h.rule.category)
        if key in seen:
            continue
        seen.add(key)
        out.append(h)
    return out


def hints(files: list[FileDiff]) -> list[str]:
    msgs = [h.as_hint() for h in scan(files)]
    msgs += [f"{p}: new code but the PR changes no test file" for p in missing_test_candidates(files)]
    return msgs
