"""Unified-diff parsing and chunking.

GitHub returns a PR as one unified diff (`Accept: application/vnd.github.diff`). We split it per file,
keep the new-file line number of every added/context line (so findings can point at real lines), skip
generated files, and pack the files into chunks that fit one model call. A single huge file is split on
hunk boundaries, never in the middle of a hunk.
"""

from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass, field

_HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(.*)$")
_GIT_HEADER = re.compile(r"^diff --git a/(.+?) b/(.+)$")

LANGUAGES = {
    ".py": "python", ".js": "javascript", ".ts": "typescript", ".tsx": "typescript", ".jsx": "javascript",
    ".go": "go", ".java": "java", ".rb": "ruby", ".rs": "rust", ".sql": "sql", ".yml": "yaml",
    ".yaml": "yaml", ".json": "json", ".toml": "toml", ".md": "markdown", ".html": "html", ".css": "css",
    ".sh": "shell", ".tf": "terraform", ".env": "dotenv", ".cfg": "ini", ".ini": "ini",
}
SOURCE_LANGUAGES = {"python", "javascript", "typescript", "go", "java", "ruby", "rust"}


@dataclass
class DiffLine:
    kind: str                 # "+", "-" or " "
    text: str
    old_no: int | None
    new_no: int | None


@dataclass
class Hunk:
    header: str
    old_start: int
    old_len: int
    new_start: int
    new_len: int
    lines: list[DiffLine] = field(default_factory=list)

    def render(self) -> str:
        return "\n".join([self.header] + [f"{ln.kind}{ln.text}" for ln in self.lines])


@dataclass
class FileDiff:
    path: str
    old_path: str
    status: str = "modified"  # added | modified | deleted | renamed
    is_binary: bool = False
    hunks: list[Hunk] = field(default_factory=list)

    @property
    def additions(self) -> int:
        return sum(1 for h in self.hunks for ln in h.lines if ln.kind == "+")

    @property
    def deletions(self) -> int:
        return sum(1 for h in self.hunks for ln in h.lines if ln.kind == "-")

    @property
    def language(self) -> str:
        name = self.path.rsplit("/", 1)[-1]
        if name.startswith(".env"):
            return "dotenv"
        ext = "." + name.rsplit(".", 1)[-1] if "." in name else ""
        return LANGUAGES.get(ext.lower(), "text")

    @property
    def is_test(self) -> bool:
        return is_test_path(self.path)

    def added_lines(self) -> list[DiffLine]:
        return [ln for h in self.hunks for ln in h.lines if ln.kind == "+"]

    def changed_new_lines(self) -> set[int]:
        """New-file line numbers that a review comment may legally point at (added or context lines)."""
        return {ln.new_no for h in self.hunks for ln in h.lines if ln.new_no is not None}

    def render(self, hunks: list[Hunk] | None = None) -> str:
        head = f"--- a/{self.old_path}\n+++ b/{self.path}"
        if self.is_binary:
            return head + "\n(binary file)"
        return head + "\n" + "\n".join(h.render() for h in (hunks if hunks is not None else self.hunks))

    def render_numbered(self, hunks: list[Hunk] | None = None) -> str:
        """The form the model sees: every line prefixed with its new-file line number."""
        out = [f"### {self.path} ({self.status}, +{self.additions}/-{self.deletions}, {self.language})"]
        for h in hunks if hunks is not None else self.hunks:
            out.append(h.header)
            for ln in h.lines:
                no = f"{ln.new_no:>5}" if ln.new_no is not None else "     "
                out.append(f"{no} {ln.kind}{ln.text}")
        return "\n".join(out)


def is_test_path(path: str) -> bool:
    p = path.lower()
    name = p.rsplit("/", 1)[-1]
    return (
        p.startswith(("tests/", "test/")) or "/tests/" in p or "/test/" in p or "__tests__" in p
        or name.startswith("test_") or name.endswith(("_test.py", ".test.ts", ".test.js", ".spec.ts", ".spec.js", "_test.go"))
    )


def parse_diff(text: str) -> list[FileDiff]:
    """Parse a unified diff (git format or plain ---/+++ format) into files, hunks and numbered lines."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    files: list[FileDiff] = []
    current: FileDiff | None = None
    hunk: Hunk | None = None
    old_no = new_no = 0
    lines = text.split("\n")
    i = 0
    while i < len(lines):
        line = lines[i]
        m = _GIT_HEADER.match(line)
        if m:
            current = FileDiff(path=m.group(2), old_path=m.group(1))
            files.append(current)
            hunk = None
        elif line.startswith("--- ") and i + 1 < len(lines) and lines[i + 1].startswith("+++ ") and (
            hunk is None or _remaining(hunk) <= 0
        ):
            old = _strip_prefix(line[4:])
            new = _strip_prefix(lines[i + 1][4:])
            if current is None or current.hunks:
                current = FileDiff(path=new if new != "/dev/null" else old, old_path=old)
                files.append(current)
            if old == "/dev/null":
                current.status, current.old_path = "added", new
            elif new == "/dev/null":
                current.status, current.path = "deleted", old
            hunk = None
            i += 2
            continue
        elif current is not None and line.startswith("new file mode"):
            current.status = "added"
        elif current is not None and line.startswith("deleted file mode"):
            current.status = "deleted"
        elif current is not None and line.startswith("rename from "):
            current.status = "renamed"
            current.old_path = line[len("rename from "):]
        elif current is not None and line.startswith("rename to "):
            current.path = line[len("rename to "):]
        elif current is not None and (line.startswith("Binary files ") or line == "GIT binary patch"):
            current.is_binary = True
        elif current is not None and (hm := _HUNK.match(line)):
            hunk = Hunk(
                header=line, old_start=int(hm.group(1)), old_len=int(hm.group(2) or 1),
                new_start=int(hm.group(3)), new_len=int(hm.group(4) or 1),
            )
            current.hunks.append(hunk)
            old_no, new_no = hunk.old_start, hunk.new_start
        elif hunk is not None and line[:1] in {"+", "-", " "}:
            kind, body = line[0], line[1:]
            if kind == "+":
                hunk.lines.append(DiffLine("+", body, None, new_no))
                new_no += 1
            elif kind == "-":
                hunk.lines.append(DiffLine("-", body, old_no, None))
                old_no += 1
            else:
                hunk.lines.append(DiffLine(" ", body, old_no, new_no))
                old_no += 1
                new_no += 1
        elif hunk is not None and line == "" and _remaining(hunk) > 0:
            # Some tools strip the trailing space of empty context lines.
            hunk.lines.append(DiffLine(" ", "", old_no, new_no))
            old_no += 1
            new_no += 1
        i += 1
    return files


def _remaining(h: Hunk) -> int:
    seen_new = sum(1 for ln in h.lines if ln.kind != "-")
    seen_old = sum(1 for ln in h.lines if ln.kind != "+")
    return max(h.new_len - seen_new, h.old_len - seen_old)


def _strip_prefix(p: str) -> str:
    p = p.split("\t", 1)[0].strip()
    if p.startswith(("a/", "b/")):
        return p[2:]
    return p


def filter_files(files: list[FileDiff], ignored: tuple[str, ...] | list[str]) -> tuple[list[FileDiff], list[str]]:
    """Drop generated / vendored / binary / deleted files. Returns (kept, skipped paths)."""
    kept, skipped = [], []
    for f in files:
        if f.is_binary or f.status == "deleted" or any(fnmatch.fnmatch(f.path, pat) for pat in ignored):
            skipped.append(f.path)
        else:
            kept.append(f)
    return kept, skipped


@dataclass
class Chunk:
    """One unit of work for the model: whole files, or consecutive hunks of one large file."""

    parts: list[tuple[FileDiff, list[Hunk]]]

    @property
    def paths(self) -> list[str]:
        return [f.path for f, _ in self.parts]

    def render(self) -> str:
        return "\n\n".join(f.render_numbered(h) for f, h in self.parts)

    def __len__(self) -> int:
        return len(self.render())


def chunk_files(files: list[FileDiff], max_chars: int) -> list[Chunk]:
    """Greedy packing: small files share a chunk; a file larger than `max_chars` is split by hunks."""
    if max_chars < 200:
        raise ValueError("max_chars is too small to hold a file header and a hunk")
    chunks: list[Chunk] = []
    current: list[tuple[FileDiff, list[Hunk]]] = []
    size = 0

    def flush():
        nonlocal current, size
        if current:
            chunks.append(Chunk(current))
        current, size = [], 0

    for f in files:
        whole = len(f.render_numbered()) + 2
        if whole <= max_chars:
            if size + whole > max_chars:
                flush()
            current.append((f, f.hunks))
            size += whole
            continue
        # A big file gets its own chunk(s), split on hunk boundaries.
        flush()
        group: list[Hunk] = []
        for h in f.hunks:
            candidate = group + [h]
            if group and len(f.render_numbered(candidate)) > max_chars:
                chunks.append(Chunk([(f, group)]))
                group = [h]
            else:
                group = candidate
        if group:
            chunks.append(Chunk([(f, group)]))
    flush()
    return chunks


def missing_test_candidates(files: list[FileDiff]) -> list[str]:
    """Source files with added code when the PR touches no test file at all (a cheap, explainable heuristic)."""
    if any(f.is_test for f in files):
        return []
    return [
        f.path for f in files
        if f.language in SOURCE_LANGUAGES and not f.is_test and f.additions >= 5
        and not f.path.endswith(("__init__.py", "settings.py", "config.py", "conftest.py"))
    ]


def diff_stats(files: list[FileDiff]) -> dict:
    return {
        "files": len(files),
        "additions": sum(f.additions for f in files),
        "deletions": sum(f.deletions for f in files),
    }
