import json
import unittest

from fastapi.testclient import TestClient

from prcopilot.github import FakeGitHubClient, sign
from prcopilot.reviewer import ClaudeReviewer
from prcopilot.storage import Store
from prcopilot.web import create_app
from tests.helpers import SMALL_DIFF, FakeAnthropic, demo_settings, diff, fake_message

SECRET = "webhook-test-secret"


def pr_event(action="opened", number=42, draft=False):
    return {
        "action": action,
        "number": number,
        "repository": {"full_name": "acme/shop"},
        "pull_request": {"number": number, "title": "Add search", "body": "Closes #1", "draft": draft,
                         "user": {"login": "elena-p"}, "head": {"sha": "abc123"}},
    }


class ApiTestCase(unittest.TestCase):
    def setUp(self):
        self.github = FakeGitHubClient(diffs={("acme/shop", 42): diff("sql-injection-flask.diff")})
        self.store = Store(":memory:")
        self.settings = demo_settings(webhook_secret=SECRET)
        self.app = create_app(self.settings, store=self.store, github=self.github)
        self.client = TestClient(self.app)

    def tearDown(self):
        self.client.close()
        self.store.close()

    def post_webhook(self, payload, event="pull_request", secret=SECRET, delivery=None):
        body = json.dumps(payload).encode()
        headers = {"X-GitHub-Event": event, "Content-Type": "application/json"}
        if secret is not None:
            headers["X-Hub-Signature-256"] = sign(secret, body)
        if delivery:
            headers["X-GitHub-Delivery"] = delivery
        return self.client.post("/webhooks/github", content=body, headers=headers)


class HealthAndConfigTest(ApiTestCase):
    def test_healthz(self):
        r = self.client.get("/healthz")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["status"], "ok")
        self.assertEqual(r.json()["mode"], "demo")

    def test_config_reports_demo_and_fake_github(self):
        c = self.client.get("/api/config").json()
        self.assertEqual((c["mode"], c["model"], c["posts_to_github"]), ("demo", "claude-opus-5-5", False))

    def test_dashboard_and_openapi_are_served(self):
        self.assertIn("PR Copilot", self.client.get("/").text)
        self.assertEqual(self.client.get("/static/app.js").status_code, 200)
        paths = self.client.get("/openapi.json").json()["paths"]
        for p in ("/webhooks/github", "/api/reviews", "/api/reviews/{review_id}", "/api/stats", "/healthz"):
            self.assertIn(p, paths)


class WebhookTest(ApiTestCase):
    def test_pull_request_is_reviewed_and_posted(self):
        r = self.post_webhook(pr_event())
        self.assertEqual(r.status_code, 202)
        review_id = r.json()["review_id"]
        review = self.client.get(f"/api/reviews/{review_id}").json()   # background task already ran
        self.assertEqual(review["status"], "completed")
        self.assertEqual(review["risk"], "high")
        self.assertEqual((review["repo"], review["pr_number"], review["author"]), ("acme/shop", 42, "elena-p"))
        self.assertTrue(review["posted"])
        self.assertIn("diff --git", review["diff"])
        [post] = self.github.posted
        self.assertEqual((post["repo"], post["number"], post["commit_id"]), ("acme/shop", 42, "abc123"))
        self.assertTrue(post["body"].startswith("<!-- pr-copilot -->"))
        self.assertTrue(all(c["line"] > 0 for c in post["comments"]))

    def test_invalid_signature_is_rejected(self):
        self.assertEqual(self.post_webhook(pr_event(), secret="wrong").status_code, 401)
        self.assertEqual(self.post_webhook(pr_event(), secret=None).status_code, 401)
        self.assertEqual(self.client.get("/api/reviews").json()["items"], [])

    def test_ping(self):
        r = self.post_webhook({"zen": "Keep it logically awesome."}, event="ping")
        self.assertEqual((r.status_code, r.json()["status"]), (200, "pong"))

    def test_other_events_and_actions_are_ignored(self):
        self.assertEqual(self.post_webhook({"ref": "main"}, event="push").json()["status"], "ignored")
        self.assertEqual(self.post_webhook(pr_event(action="closed")).json()["status"], "ignored")
        self.assertEqual(self.post_webhook(pr_event(draft=True)).json()["status"], "ignored")
        self.assertEqual(self.github.posted, [])

    def test_synchronize_triggers_a_new_review(self):
        self.post_webhook(pr_event())
        self.post_webhook(pr_event(action="synchronize"))
        self.assertEqual(len(self.client.get("/api/reviews?repo=acme/shop").json()["items"]), 2)

    def test_duplicate_delivery_is_processed_once(self):
        self.post_webhook(pr_event(), delivery="d-1")
        r = self.post_webhook(pr_event(), delivery="d-1")
        self.assertEqual(r.json()["status"], "duplicate")
        self.assertEqual(len(self.github.posted), 1)

    def test_malformed_payloads(self):
        bad = pr_event()
        del bad["repository"]
        self.assertEqual(self.post_webhook(bad).status_code, 422)
        body = b"not json"
        r = self.client.post("/webhooks/github", content=body,
                             headers={"X-GitHub-Event": "pull_request", "X-Hub-Signature-256": sign(SECRET, body)})
        self.assertEqual(r.status_code, 400)

    def test_live_mode_without_secret_refuses_webhooks(self):
        app = create_app(demo_settings(mode="live"), store=Store(":memory:"), github=FakeGitHubClient(),
                         reviewer=ClaudeReviewer(demo_settings(mode="live"), client=FakeAnthropic([None])))
        with TestClient(app) as c:
            r = c.post("/webhooks/github", content=b"{}", headers={"X-GitHub-Event": "ping"})
        self.assertEqual(r.status_code, 503)


class ReviewsApiTest(ApiTestCase):
    def test_post_diff_returns_review(self):
        r = self.client.post("/api/reviews", json={"diff": diff("n-plus-one-orders.diff"), "title": "Totals", "repo": "acme/shop", "pr_number": 9})
        self.assertEqual(r.status_code, 201)
        body = r.json()
        self.assertEqual(body["risk"], "medium")
        self.assertEqual(body["findings"][0]["category"], "performance")
        self.assertGreater(body["cost_usd"], 0)
        self.assertTrue(body["estimated"])

    def test_post_validation(self):
        self.assertEqual(self.client.post("/api/reviews", json={"diff": "short"}).status_code, 422)
        self.assertEqual(self.client.post("/api/reviews", json={"diff": SMALL_DIFF, "repo": "not a repo"}).status_code, 422)

    def test_list_filters_and_404(self):
        self.client.post("/api/reviews", json={"diff": diff("clean-refactor.diff")})
        self.client.post("/api/reviews", json={"diff": diff("leaked-secret-config.diff")})
        items = self.client.get("/api/reviews?risk=high").json()["items"]
        self.assertEqual([i["risk"] for i in items], ["high"])
        self.assertNotIn("diff", items[0])
        self.assertEqual(self.client.get("/api/reviews?risk=extreme").status_code, 422)
        self.assertEqual(self.client.get("/api/reviews/999").status_code, 404)

    def test_failed_model_call_returns_502_and_is_stored(self):
        reviewer = ClaudeReviewer(demo_settings(mode="live"), client=FakeAnthropic([fake_message("{}", stop_reason="refusal")]))
        store = Store(":memory:")
        with TestClient(create_app(demo_settings(), store=store, reviewer=reviewer, github=FakeGitHubClient())) as c:
            r = c.post("/api/reviews", json={"diff": SMALL_DIFF})
            self.assertEqual(r.status_code, 502)
            self.assertEqual(c.get("/api/reviews?status=failed").json()["items"][0]["status"], "failed")

    def test_stats(self):
        for name in ("clean-refactor.diff", "leaked-secret-config.diff", "missing-tests-util.diff"):
            self.client.post("/api/reviews", json={"diff": diff(name)})
        s = self.client.get("/api/stats?days=7").json()
        self.assertEqual((s["reviews"], s["high_risk"], s["medium_risk"], s["low_risk"]), (3, 1, 1, 1))
        self.assertEqual(len(s["trend"]), 7)
        self.assertEqual(sum(d["low"] + d["medium"] + d["high"] for d in s["trend"]), 3)
        self.assertEqual(s["categories"]["security"]["critical"], 2)
        self.assertGreater(s["cache_hit_rate"], 0)
        self.assertGreaterEqual(s["cost_without_cache_usd"], s["total_cost_usd"])

    def test_demo_run_rotates_samples(self):
        a = self.client.post("/api/demo/run", json={}).json()
        b = self.client.post("/api/demo/run").json()
        self.assertNotEqual(a["title"], b["title"])
        self.assertEqual(self.client.post("/api/demo/run", json={"sample_id": "leaked-secret"}).json()["risk"], "high")
        self.assertEqual(self.client.post("/api/demo/run", json={"sample_id": "nope"}).status_code, 404)
        self.assertEqual(len(self.github.posted), 3)    # demo reviews only go to the fake client

    def test_samples_endpoint(self):
        self.assertEqual(len(self.client.get("/api/samples").json()["items"]), 6)


class SeedTest(unittest.TestCase):
    def test_demo_history_is_seeded_once_on_startup(self):
        store = Store(":memory:")
        app = create_app(demo_settings(seed_demo_data=True), store=store, github=FakeGitHubClient())
        with TestClient(app) as c:
            n = len(c.get("/api/reviews?limit=200").json()["items"])
            self.assertGreater(n, 20)
            stats = c.get("/api/stats").json()
            self.assertEqual(stats["reviews"], n)
            self.assertTrue(stats["estimated"])
        with TestClient(app) as c:     # second startup does not duplicate
            self.assertEqual(len(c.get("/api/reviews?limit=200").json()["items"]), n)


if __name__ == "__main__":
    unittest.main()
