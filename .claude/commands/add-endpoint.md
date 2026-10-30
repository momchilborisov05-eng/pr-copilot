---
description: Add a REST endpoint with models, errors, tests and docs
argument-hint: <method path and behaviour>
---
Add the endpoint described in $ARGUMENTS to prcopilot/web/app.py.

- Request/response models with Pydantic and explicit Field limits.
- Errors as HTTPException with a clear message (404, 409, 422, 502).
- Tests in tests/test_api.py using FastAPI's TestClient: happy path, validation error, not found.
- Update the endpoint table in README.md.

Run `python -m unittest discover -s tests` and show the result.
