"""Bundled sample PRs and their canned reviews (demo mode).

The reviews are hand-written to look like what the live reviewer returns for these diffs, and they go through
the same schema validation as model output. They are labelled as demo output everywhere they appear.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from .config import EXAMPLES_DIR


@dataclass(frozen=True)
class Sample:
    id: str
    file: str
    title: str
    body: str
    author: str
    repo: str
    review: dict

    @property
    def path(self):
        return EXAMPLES_DIR / self.file

    def diff(self) -> str:
        return self.path.read_text(encoding="utf-8")


def fingerprint(diff_text: str) -> str:
    """Hash of the diff that ignores line endings and trailing whitespace (git may rewrite both)."""
    lines = [ln.rstrip() for ln in diff_text.replace("\r\n", "\n").split("\n")]
    while lines and not lines[-1]:
        lines.pop()
    return hashlib.sha256("\n".join(lines).encode()).hexdigest()


SAMPLES: list[Sample] = [
    Sample(
        id="sql-injection",
        file="sql-injection-flask.diff",
        title="Add customer search endpoint for the support console",
        body="Support agents need to find customers by name. Adds GET /customers/search and a delete endpoint.",
        author="dimitar-k",
        repo="acme/support-api",
        review={
            "summary": (
                "Adds a customer search endpoint, a delete endpoint and protects the existing detail route with "
                "login_required. The search query is built with an f-string from two request parameters, which "
                "is a SQL injection, and both new endpoints are reachable without authentication."
            ),
            "risk": "high",
            "findings": [
                {
                    "file": "app/routes/customers.py", "line": 29, "severity": "critical", "category": "security",
                    "title": "SQL injection through the q and sort parameters",
                    "explanation": (
                        "`term` and `sort` come straight from request.args and are interpolated into the SQL "
                        "string. `?q=' OR 1=1 --` dumps every customer, and `sort` allows arbitrary SQL after "
                        "ORDER BY."
                    ),
                    "suggestion": (
                        "Bind the search term and whitelist the sort column:\n"
                        "SORTS = {\"name\", \"city\", \"id\"}\n"
                        "order = sort if sort in SORTS else \"name\"\n"
                        "conn.execute(f\"SELECT ... WHERE name LIKE ? ORDER BY {order}\", (f\"%{term}%\",))"
                    ),
                },
                {
                    "file": "app/routes/customers.py", "line": 35, "severity": "critical", "category": "security",
                    "title": "Delete endpoint has no authentication or authorization",
                    "explanation": (
                        "The PR adds @login_required to the read endpoint but not to DELETE, so anyone who can "
                        "reach the API can delete customers by id."
                    ),
                    "suggestion": "Add @login_required and a role check (for example @require_role(\"admin\")).",
                },
                {
                    "file": "app/routes/customers.py", "line": 21, "severity": "major", "category": "security",
                    "title": "Search exposes customer emails without login",
                    "explanation": "The search route returns name, email and city for any caller.",
                    "suggestion": "Protect the route with @login_required and paginate the result (LIMIT 50).",
                },
                {
                    "file": "app/routes/customers.py", "line": 0, "severity": "major", "category": "tests",
                    "title": "No tests for the new endpoints",
                    "explanation": "Search and delete are new behaviour and the PR adds no test.",
                    "suggestion": "Add tests for search (including a quote in q), invalid sort and unauthenticated delete.",
                },
            ],
            "missing_tests": [
                "Search with a quote or SQL fragment in q returns only matching rows",
                "DELETE /customers/<id> without a session returns 401",
                "Unknown sort values fall back to name",
            ],
            "changelog": "Added: customer search and delete endpoints (support console).",
        },
    ),
    Sample(
        id="missing-tests",
        file="missing-tests-util.diff",
        title="Add slugify and pagination helpers to the catalogue",
        body="Product pages get slugs and the product list is paginated (20 per page).",
        author="elena-p",
        repo="acme/shop",
        review={
            "summary": (
                "Introduces slugify, paginate and truncate_words helpers and uses them in GET /products. The "
                "helpers are reasonable, but paginate has an off-by-one in total_pages and accepts page 0 or "
                "negative pages, and none of the new code has a test."
            ),
            "risk": "medium",
            "findings": [
                {
                    "file": "shop/utils/text.py", "line": 20, "severity": "major", "category": "bug",
                    "title": "total_pages rounds down, so the last partial page is unreachable",
                    "explanation": (
                        "With 45 items and 20 per page, `45 // 20` is 2, so has_next is False on page 2 and the "
                        "last 5 products never show up in the UI."
                    ),
                    "suggestion": "total_pages = max(1, math.ceil(len(items) / per_page))",
                },
                {
                    "file": "shop/utils/text.py", "line": 18, "severity": "minor", "category": "bug",
                    "title": "page=0 or a negative page returns items from the end of the list",
                    "explanation": "page=0 makes start -20, and Python slicing turns that into the last 20 items.",
                    "suggestion": "Validate in the endpoint: `page: int = Query(1, ge=1)`.",
                },
                {
                    "file": "shop/views/catalogue.py", "line": 13, "severity": "minor", "category": "performance",
                    "title": "Slugs are recomputed for the whole catalogue on every request",
                    "explanation": "slugify runs for every product before pagination, not only for the 20 returned.",
                    "suggestion": "Paginate first, then add slugs to the page, or store the slug on the product.",
                },
                {
                    "file": "shop/utils/text.py", "line": 0, "severity": "major", "category": "tests",
                    "title": "New helpers have no unit tests",
                    "explanation": "Three pure functions with edge cases (unicode, empty input, partial pages) and no tests.",
                    "suggestion": "Add tests/test_text.py covering slugify unicode input, paginate boundaries and truncate_words.",
                },
            ],
            "missing_tests": [
                "slugify('Cafe Creme 250g') and non-Latin input",
                "paginate with 45 items: page 3 exists and has 5 items",
                "paginate with page=0 is rejected",
                "truncate_words when text is shorter than the limit",
            ],
            "changelog": "Added: product slugs and paginated GET /products (20 per page).",
        },
    ),
    Sample(
        id="n-plus-one",
        file="n-plus-one-orders.diff",
        title="Show customer and order totals on the ops dashboard",
        body="The dashboard now shows customer name, item count and total for the 50 most recent orders.",
        author="georgi-n",
        repo="acme/shop",
        review={
            "summary": (
                "Extends recent_orders with customer name, item count and total, and adds revenue_today. The "
                "loop runs two queries per order (items and customer), so 50 orders cost 101 round trips, and "
                "revenue_today does not filter by date."
            ),
            "risk": "medium",
            "findings": [
                {
                    "file": "backend/services/dashboard.py", "line": 13, "severity": "major", "category": "performance",
                    "title": "N+1 queries: items and customer are loaded per order",
                    "explanation": (
                        "Each iteration runs a SELECT for OrderItem and a session.get for Customer, so the "
                        "dashboard does 1 + 2N queries and gets slower as the limit grows."
                    ),
                    "suggestion": (
                        "Eager-load both relationships in the first query:\n"
                        "select(Order).options(selectinload(Order.items), joinedload(Order.customer))"
                    ),
                },
                {
                    "file": "backend/services/dashboard.py", "line": 29, "severity": "major", "category": "bug",
                    "title": "revenue_today sums all orders ever, not today's",
                    "explanation": "The statement has no WHERE clause on Order.created_at.",
                    "suggestion": "Add .where(Order.created_at >= start_of_day_utc) and a test with yesterday's order.",
                },
                {
                    "file": "backend/services/dashboard.py", "line": 20, "severity": "minor", "category": "bug",
                    "title": "customer.name fails for orders without a customer",
                    "explanation": "session.get returns None for guest checkouts or deleted customers.",
                    "suggestion": "Use `customer.name if customer else \"Guest\"` (or an outer join).",
                },
            ],
            "missing_tests": ["revenue_today ignores orders from previous days"],
            "changelog": "Changed: the ops dashboard shows customer, item count and total per order.",
        },
    ),
    Sample(
        id="leaked-secret",
        file="leaked-secret-config.diff",
        title="Configure S3 uploads for staging",
        body="Product images go to the staging bucket. Quick fix so QA can test uploads today.",
        author="ivan-t",
        repo="acme/shop",
        review={
            "summary": (
                "Configures S3 uploads for staging, but commits an AWS access key pair and a database password "
                "and hard-codes DEBUG, debug logging and allow-all CORS. The keys must be treated as leaked "
                "and rotated even if this PR is closed."
            ),
            "risk": "high",
            "findings": [
                {
                    "file": "config/settings.py", "line": 16, "severity": "critical", "category": "security",
                    "title": "AWS access key pair committed",
                    "explanation": (
                        "AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY are string literals. They are now in git "
                        "history and in every fork and CI log of this branch."
                    ),
                    "suggestion": (
                        "Deactivate the key in IAM, then read both values from the environment: "
                        "AWS_ACCESS_KEY_ID = os.environ[\"AWS_ACCESS_KEY_ID\"]. Prefer an IAM role on the server."
                    ),
                },
                {
                    "file": "config/settings.py", "line": 8, "severity": "critical", "category": "security",
                    "title": "Staging database password in the default DATABASE_URL",
                    "explanation": "The fallback connection string contains the admin password for db.staging.internal.",
                    "suggestion": "Revert to os.environ[\"DATABASE_URL\"] so a missing variable fails fast.",
                },
                {
                    "file": "config/settings.py", "line": 5, "severity": "major", "category": "security",
                    "title": "DEBUG is hard-coded to True",
                    "explanation": "This replaces an environment-driven flag, so production would run with debug pages.",
                    "suggestion": "Keep DEBUG = os.environ.get(\"DEBUG\", \"false\").lower() == \"true\".",
                },
                {
                    "file": "config/settings.py", "line": 20, "severity": "major", "category": "security",
                    "title": "CORS allows every origin",
                    "explanation": "Combined with cookie sessions, any site can make authenticated requests from a user's browser.",
                    "suggestion": "Set CORS_ALLOWED_ORIGINS from the environment per deployment.",
                },
                {
                    "file": "config/settings.py", "line": 13, "severity": "minor", "category": "style",
                    "title": "LOG_LEVEL hard-coded to DEBUG",
                    "explanation": "Debug logs can include request bodies and tokens and are noisy in production.",
                    "suggestion": "Keep reading LOG_LEVEL from the environment with INFO as the default.",
                },
            ],
            "missing_tests": [],
            "changelog": "Security: remove committed AWS and database credentials (rotate before merging).",
        },
    ),
    Sample(
        id="clean-refactor",
        file="clean-refactor.diff",
        title="Extract email validation into accounts.validators",
        body="Removes duplicated email checks from the signup and profile forms. No behaviour change except the error type.",
        author="maria-s",
        repo="acme/accounts",
        review={
            "summary": (
                "Moves the duplicated email check into normalize_email with a dedicated ValidationError and "
                "adds focused unit tests. ValidationError subclasses ValueError, so existing callers keep "
                "working. Looks good to merge."
            ),
            "risk": "low",
            "findings": [
                {
                    "file": "accounts/validators.py", "line": 16, "severity": "info", "category": "style",
                    "title": "Error message echoes raw user input",
                    "explanation": "Fine for forms, but if this message reaches logs it will contain user emails.",
                    "suggestion": "Optionally use a fixed message and attach the value as an attribute.",
                },
            ],
            "missing_tests": [],
            "changelog": "Changed: email validation is shared by the signup and profile forms.",
        },
    ),
    Sample(
        id="async-blocking",
        file="async-blocking-call.diff",
        title="Return prices in the customer's currency",
        body="Adds ?currency= to GET /pricing/{sku} using the exchange-rate API.",
        author="nikolay-v",
        repo="acme/pricing-service",
        review={
            "summary": (
                "Adds currency conversion and a 404 for unknown SKUs. The exchange-rate call uses the blocking "
                "requests library inside an async endpoint without a timeout, and any failure silently prices "
                "the product at a 1:1 rate in the wrong currency."
            ),
            "risk": "medium",
            "findings": [
                {
                    "file": "api/routers/pricing.py", "line": 19, "severity": "major", "category": "performance",
                    "title": "Blocking requests.get inside an async endpoint",
                    "explanation": (
                        "requests blocks the event loop thread, so one slow rates call stalls every concurrent "
                        "request on this worker."
                    ),
                    "suggestion": (
                        "async with httpx.AsyncClient(timeout=5) as client:\n"
                        "    response = await client.get(RATES_URL, params={\"base\": \"EUR\"})"
                    ),
                },
                {
                    "file": "api/routers/pricing.py", "line": 22, "severity": "major", "category": "bug",
                    "title": "Failures fall back to a 1:1 rate and return a wrong price",
                    "explanation": (
                        "`except Exception: rate = 1.0` turns an outage or an unknown currency into a confident "
                        "but wrong price, e.g. 20 EUR shown as 20 USD."
                    ),
                    "suggestion": "Return 503 (or 400 for an unknown currency) instead of guessing, and log the error.",
                },
                {
                    "file": "api/routers/pricing.py", "line": 19, "severity": "minor", "category": "bug",
                    "title": "No timeout on the upstream call",
                    "explanation": "Without a timeout a hung upstream holds the request open indefinitely.",
                    "suggestion": "Set timeout=5 and cache rates for a few minutes.",
                },
                {
                    "file": "api/routers/pricing.py", "line": 0, "severity": "minor", "category": "tests",
                    "title": "Conversion path is untested",
                    "explanation": "No test covers a non-EUR currency, an unknown currency or an upstream failure.",
                    "suggestion": "Mock the rates client and test USD, an unknown code and a timeout.",
                },
            ],
            "missing_tests": [
                "currency=USD multiplies by the fetched rate",
                "unknown currency returns 400",
                "rates API timeout returns 503",
            ],
            "changelog": "Added: ?currency= on GET /pricing/{sku} with live exchange rates.",
        },
    ),
]

_BY_FINGERPRINT: dict[str, Sample] | None = None


def find_sample(diff_text: str) -> Sample | None:
    global _BY_FINGERPRINT
    if _BY_FINGERPRINT is None:
        _BY_FINGERPRINT = {fingerprint(s.diff()): s for s in SAMPLES if s.path.exists()}
    return _BY_FINGERPRINT.get(fingerprint(diff_text))


def get_sample(sample_id: str) -> Sample | None:
    return next((s for s in SAMPLES if s.id == sample_id), None)
