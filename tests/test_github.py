import json
import unittest

import httpx

from prcopilot.github import (
    MARKER,
    FakeGitHubClient,
    GitHubError,
    RestGitHubClient,
    inline_comments,
    render_markdown,
    review_event,
    sign,
    verify_signature,
)
from prcopilot.schema import Review

BODY = b'{"action":"opened","number":7}'


def review(**kw):
    base = dict(summary="Adds search. SQL injection in the query.", risk="high", changelog="Added: search",
                missing_tests=["search with a quote"], files_reviewed=["app.py"], files_skipped=["poetry.lock"],
                findings=[
                    dict(file="app.py", line=29, severity="critical", category="security", title="SQL injection | f-string",
                         explanation="User input in SQL.", suggestion="Use ? placeholders."),
                    dict(file="app.py", line=0, severity="major", category="tests", title="No tests",
                         explanation="New endpoint untested.", suggestion="Add tests."),
                    dict(file="app.py", line=4, severity="info", category="style", title="Naming",
                         explanation="Name is vague.", suggestion="Rename."),
                ])
    base.update(kw)
    return Review(**base)


class SignatureTest(unittest.TestCase):
    def test_valid_signature(self):
        self.assertTrue(verify_signature("s3cret", BODY, sign("s3cret", BODY)))

    def test_known_vector_from_github_docs(self):
        # Example from GitHub's "Validating webhook deliveries" documentation.
        self.assertTrue(verify_signature(
            "It's a Secret to Everybody", b"Hello, World!",
            "sha256=757107ea0eb2509fc211221cce984b8a37570b6d7586c22c46f4379c8b043e17"))

    def test_wrong_secret_or_tampered_body(self):
        self.assertFalse(verify_signature("other", BODY, sign("s3cret", BODY)))
        self.assertFalse(verify_signature("s3cret", BODY + b" ", sign("s3cret", BODY)))

    def test_missing_malformed_or_sha1_header(self):
        for header in (None, "", "abc", "sha1=" + "0" * 40, sign("s3cret", BODY).replace("sha256=", "")):
            self.assertFalse(verify_signature("s3cret", BODY, header), header)

    def test_empty_secret_never_verifies(self):
        self.assertFalse(verify_signature("", BODY, sign("", BODY)))


class RenderTest(unittest.TestCase):
    def test_markdown_contains_everything_a_reviewer_needs(self):
        md = render_markdown(review(), model="claude-opus-5-5", mode="live", cost_usd=0.0123)
        self.assertTrue(md.startswith(MARKER))
        for part in ("risk 🔴 High", "`app.py:29`", "SQL injection \\| f-string", "- [ ] search with a quote",
                     "> Added: search", "Model: claude-opus-5-5", "$0.0123", "poetry.lock"):
            self.assertIn(part, md)

    def test_clean_review_and_demo_footer(self):
        md = render_markdown(review(risk="low", findings=[], missing_tests=[]), model="m", mode="demo")
        self.assertIn("No findings.", md)
        self.assertIn("Demo review", md)

    def test_inline_comments_skip_file_level_and_info(self):
        comments = inline_comments(review())
        self.assertEqual([(c["path"], c["line"], c["side"]) for c in comments], [("app.py", 29, "RIGHT")])

    def test_review_event_only_requests_changes_when_opted_in(self):
        self.assertEqual(review_event(review()), "COMMENT")
        self.assertEqual(review_event(review(), "high"), "REQUEST_CHANGES")
        self.assertEqual(review_event(review(risk="medium", findings=[]), "high"), "COMMENT")


class RestClientTest(unittest.TestCase):
    def client(self, handler):
        return RestGitHubClient("ghs_test", "https://api.github.test", http=httpx.Client(transport=httpx.MockTransport(handler)))

    def test_get_diff_uses_diff_media_type_and_token(self):
        seen = {}

        def handler(request):
            seen.update(url=str(request.url), accept=request.headers["accept"], auth=request.headers["authorization"])
            return httpx.Response(200, text="diff --git a/x b/x\n")

        self.assertTrue(self.client(handler).get_pr_diff("acme/shop", 7).startswith("diff --git"))
        self.assertEqual(seen["url"], "https://api.github.test/repos/acme/shop/pulls/7")
        self.assertEqual(seen["accept"], "application/vnd.github.diff")
        self.assertEqual(seen["auth"], "Bearer ghs_test")

    def test_post_review_payload(self):
        bodies = []

        def handler(request):
            bodies.append(json.loads(request.content))
            return httpx.Response(200, json={"id": 1})

        self.client(handler).post_review("acme/shop", 7, body="hi", event="COMMENT",
                                         comments=[{"path": "a.py", "line": 1, "side": "RIGHT", "body": "x"}], commit_id="abc")
        self.assertEqual(bodies[0]["commit_id"], "abc")
        self.assertEqual(bodies[0]["event"], "COMMENT")

    def test_422_retries_without_inline_comments(self):
        bodies = []

        def handler(request):
            bodies.append(json.loads(request.content))
            return httpx.Response(422 if len(bodies) == 1 else 200, json={})

        self.client(handler).post_review("acme/shop", 7, body="hi", event="COMMENT", comments=[{"path": "a.py"}])
        self.assertEqual([len(b["comments"]) for b in bodies], [1, 0])

    def test_errors_raise_github_error(self):
        c = self.client(lambda r: httpx.Response(404, json={}))
        with self.assertRaises(GitHubError):
            c.get_pr_diff("acme/shop", 1)
        with self.assertRaises(GitHubError):
            c.post_review("acme/shop", 1, body="x", event="COMMENT", comments=[])

    def test_token_required(self):
        with self.assertRaises(GitHubError):
            RestGitHubClient("")


class FakeClientTest(unittest.TestCase):
    def test_records_calls_and_returns_configured_diff(self):
        fake = FakeGitHubClient(diffs={("a/b", 1): "DIFF"})
        self.assertEqual(fake.get_pr_diff("a/b", 1), "DIFF")
        fake.post_review("a/b", 1, body="x", event="COMMENT", comments=[])
        self.assertEqual(len(fake.posted), 1)
        self.assertEqual(fake.calls[0]["method"], "get_pr_diff")

    def test_unknown_pr_falls_back_to_a_sample_diff(self):
        self.assertIn("diff --git", FakeGitHubClient().get_pr_diff("x/y", 3))


if __name__ == "__main__":
    unittest.main()
