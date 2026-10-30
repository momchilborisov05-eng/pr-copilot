import unittest
from datetime import timedelta

from prcopilot.config import Settings
from prcopilot.reviewer import DemoReviewer
from prcopilot.storage import Store, utcnow
from tests.helpers import demo_settings, diff


class SettingsTest(unittest.TestCase):
    def test_demo_without_key(self):
        s = Settings.from_env({})
        self.assertEqual((s.mode, s.model, s.effort), ("demo", "claude-opus-5-5", "medium"))

    def test_auto_switches_to_live_with_key(self):
        self.assertEqual(Settings.from_env({"ANTHROPIC_API_KEY": "sk-test"}).mode, "live")

    def test_forced_demo_and_live_without_key(self):
        self.assertEqual(Settings.from_env({"ANTHROPIC_API_KEY": "sk-test", "PRCOPILOT_MODE": "demo"}).mode, "demo")
        self.assertEqual(Settings.from_env({"PRCOPILOT_MODE": "live"}).mode, "demo")

    def test_overrides_and_bad_numbers(self):
        s = Settings.from_env({"CLAUDE_MODEL": "claude-sonnet-5-5", "PRCOPILOT_MAX_CHUNK_CHARS": "abc",
                               "PRCOPILOT_POST_TO_GITHUB": "false", "GITHUB_API_URL": "https://ghe.example/api/v3/"})
        self.assertEqual(s.model, "claude-sonnet-5-5")
        self.assertEqual(s.max_chunk_chars, 24_000)
        self.assertFalse(s.post_to_github)
        self.assertEqual(s.github_api_url, "https://ghe.example/api/v3")


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.store = Store(":memory:")
        self.reviewer = DemoReviewer(demo_settings())

    def tearDown(self):
        self.store.close()

    def add(self, name, created=None):
        rid = self.store.create_review(repo="acme/shop", pr_number=1, title=name, source="api", diff=diff(name),
                                       created_at=created)
        self.store.complete_review(rid, self.reviewer.review(diff(name)))
        return rid

    def test_round_trip(self):
        rid = self.add("leaked-secret-config.diff")
        r = self.store.get_review(rid)
        self.assertEqual((r["status"], r["risk"], r["mode"]), ("completed", "high", "demo"))
        self.assertEqual(r["findings"][0]["severity"], "critical")
        self.assertEqual(r["files_reviewed"], ["config/settings.py", ".env.example"])
        self.assertGreater(r["cost_usd"], 0)

    def test_findings_are_replaced_on_re_complete(self):
        rid = self.add("clean-refactor.diff")
        self.store.complete_review(rid, self.reviewer.review(diff("clean-refactor.diff")))
        self.assertEqual(len(self.store.get_review(rid)["findings"]), 1)

    def test_failed_review(self):
        rid = self.store.create_review(repo="a/b", pr_number=2, source="webhook")
        self.store.fail_review(rid, "boom")
        r = self.store.list_reviews(status="failed")[0]
        self.assertEqual((r["status"], r["error"], r["findings_count"]), ("failed", "boom", 0))
        self.assertEqual(self.store.stats()["failed"], 1)

    def test_list_counts_serious_findings(self):
        self.add("sql-injection-flask.diff")
        [r] = self.store.list_reviews()
        self.assertEqual((r["findings_count"], r["serious_count"]), (4, 4))

    def test_trend_buckets_by_day_and_ignores_old_reviews(self):
        self.add("clean-refactor.diff", created=utcnow() - timedelta(days=2))
        self.add("sql-injection-flask.diff")
        self.add("missing-tests-util.diff", created=utcnow() - timedelta(days=40))
        trend = self.store.stats(days=7)["trend"]
        self.assertEqual(trend[-1]["high"], 1)
        self.assertEqual(trend[-3]["low"], 1)
        self.assertEqual(sum(d["medium"] for d in trend), 0)

    def test_cascade_delete(self):
        rid = self.add("sql-injection-flask.diff")
        with self.store.conn:
            self.store.conn.execute("DELETE FROM reviews WHERE id=?", (rid,))
        self.assertEqual(self.store.conn.execute("SELECT COUNT(*) FROM findings").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
