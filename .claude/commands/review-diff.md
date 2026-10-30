---
description: Self-review the branch against the project's review rubric before opening a PR
---
Review `git diff main...HEAD` using the rubric in docs/ai-sdlc.md (section 3) and prcopilot/prompts.py.

Output:
- risk: low | medium | high
- findings: file:line, severity, category, one-line title, a concrete fix
- missing tests

Only report what the diff changes. If it is clean, say so in one line.
