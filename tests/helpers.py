"""Shared test fixtures. No test touches the network or the real Claude / GitHub APIs."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from prcopilot.config import Settings

ROOT = Path(__file__).resolve().parent.parent
DIFFS = ROOT / "examples" / "diffs"


def diff(name: str) -> str:
    return (DIFFS / name).read_text(encoding="utf-8")


def demo_settings(**overrides) -> Settings:
    base = dict(mode="demo", db_path=":memory:", seed_demo_data=False, webhook_secret="")
    base.update(overrides)
    return Settings(**base)


SMALL_DIFF = """\
diff --git a/app/util.py b/app/util.py
index 1111111..2222222 100644
--- a/app/util.py
+++ b/app/util.py
@@ -1,4 +1,6 @@
 import os
+import subprocess

 def run(cmd):
-    return os.system(cmd)
+    subprocess.run(cmd, shell=True)
+    return 0
"""


def fake_message(payload: dict | str, *, stop_reason: str = "end_turn", input_tokens: int = 1200,
                 output_tokens: int = 400, cache_read: int = 0, cache_write: int = 900, model: str = "claude-opus-5-5"):
    """An object shaped like an Anthropic Message, for ClaudeReviewer tests."""
    text = payload if isinstance(payload, str) else json.dumps(payload)
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text=text)],
        stop_reason=stop_reason,
        model=model,
        usage=SimpleNamespace(input_tokens=input_tokens, output_tokens=output_tokens,
                              cache_read_input_tokens=cache_read, cache_creation_input_tokens=cache_write),
    )


class FakeAnthropic:
    """Records every `beta.messages.create` call and replays queued responses."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.requests: list[dict] = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.requests.append(kwargs)
        item = self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
        if isinstance(item, Exception):
            raise item
        return item(kwargs) if callable(item) else item
