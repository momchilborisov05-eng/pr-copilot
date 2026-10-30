"""Runtime settings, read from environment variables (and an optional .env loaded by the CLI)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXAMPLES_DIR = ROOT / "examples" / "diffs"

DEFAULT_MODEL = "claude-opus-5-5"


def _int(env: dict[str, str], name: str, default: int) -> int:
    try:
        return int(env.get(name) or default)
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    """Everything the service needs. Build it with `Settings.from_env()` or directly in tests."""

    mode: str = "demo"                     # "demo" (canned reviews, fake GitHub) or "live"
    model: str = DEFAULT_MODEL
    effort: str = "medium"
    anthropic_api_key_set: bool = False
    webhook_secret: str = ""
    github_token: str = ""
    github_api_url: str = "https://api.github.com"
    db_path: str = "pr_copilot.db"
    max_chunk_chars: int = 24_000          # one Claude call reviews at most this many diff characters
    max_files: int = 60                    # bigger PRs are reviewed partially and flagged
    post_to_github: bool = True
    seed_demo_data: bool = True
    ignored_paths: tuple[str, ...] = field(
        default=("*.lock", "package-lock.json", "poetry.lock", "*.min.js", "*.min.css", "dist/*", "vendor/*",
                 "*.diff", "*.patch", "*.png", "*.svg")      # fixtures (e.g. the sample diffs) and assets
    )

    @property
    def is_demo(self) -> bool:
        return self.mode == "demo"

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> Settings:
        env = dict(os.environ if env is None else env)
        key_set = bool(env.get("ANTHROPIC_API_KEY", "").strip())
        requested = env.get("PRCOPILOT_MODE", "auto").strip().lower()
        if requested == "live" and key_set:
            mode = "live"
        elif requested == "auto":
            mode = "live" if key_set else "demo"
        else:
            mode = "demo"                  # "demo", unknown values, or "live" without a key
        return cls(
            mode=mode,
            model=env.get("CLAUDE_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL,
            effort=env.get("CLAUDE_EFFORT", "medium").strip() or "medium",
            anthropic_api_key_set=key_set,
            webhook_secret=env.get("GITHUB_WEBHOOK_SECRET", ""),
            github_token=env.get("GITHUB_TOKEN", ""),
            github_api_url=env.get("GITHUB_API_URL", "https://api.github.com").rstrip("/"),
            db_path=env.get("PRCOPILOT_DB", "pr_copilot.db"),
            max_chunk_chars=_int(env, "PRCOPILOT_MAX_CHUNK_CHARS", 24_000),
            max_files=_int(env, "PRCOPILOT_MAX_FILES", 60),
            post_to_github=env.get("PRCOPILOT_POST_TO_GITHUB", "true").lower() not in {"0", "false", "no"},
            seed_demo_data=env.get("PRCOPILOT_SEED_DEMO", "true").lower() not in {"0", "false", "no"},
        )
