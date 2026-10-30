"""Command-line interface.

    python -m prcopilot serve [--host 127.0.0.1] [--port 8000]
    python -m prcopilot review examples/diffs/sql-injection-flask.diff [--json]
    python -m prcopilot samples
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _load_dotenv() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="prcopilot", description="PR Copilot - AI pull-request reviewer")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("serve", help="Run the API and dashboard")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8000)
    r = sub.add_parser("review", help="Review a diff file and print the result")
    r.add_argument("diff_file")
    r.add_argument("--title", default="")
    r.add_argument("--json", action="store_true", help="Print JSON instead of Markdown")
    sub.add_parser("samples", help="List the bundled sample PR diffs")
    args = p.parse_args(argv)
    _load_dotenv()

    from .config import Settings

    settings = Settings.from_env()
    if args.cmd == "serve":
        import uvicorn

        from .web import create_app

        uvicorn.run(create_app(settings), host=args.host, port=args.port)
        return 0
    if args.cmd == "samples":
        from .demo_data import SAMPLES

        for sample in SAMPLES:
            print(f"{sample.id:16} {sample.path.relative_to(Path.cwd()) if sample.path.is_relative_to(Path.cwd()) else sample.path}  {sample.title}")
        return 0

    from .github import render_markdown
    from .reviewer import make_reviewer

    outcome = make_reviewer(settings).review(Path(args.diff_file).read_text(encoding="utf-8"), title=args.title)
    if args.json:
        print(json.dumps({"review": outcome.review.model_dump(mode="json"), "usage": outcome.usage.to_dict(),
                          "cost_usd": outcome.cost_usd, "mode": outcome.mode, "model": outcome.model}, indent=2))
    else:
        sys.stdout.reconfigure(encoding="utf-8")
        print(render_markdown(outcome.review, model=outcome.model, mode=outcome.mode, cost_usd=outcome.cost_usd))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
