import unittest

from prcopilot.demo_data import SAMPLES, find_sample, fingerprint
from prcopilot.diffparse import parse_diff
from prcopilot.heuristics import hints, scan
from prcopilot.prompts import SYSTEM_PROMPT
from prcopilot.reviewer import ClaudeReviewer, DemoReviewer, ReviewError, make_reviewer
from prcopilot.schema import REVIEW_JSON_SCHEMA, validate_chunk_review
from tests.helpers import SMALL_DIFF, FakeAnthropic, demo_settings, diff, fake_message

GOOD = {
    "summary": "Replaces os.system with subprocess but keeps shell=True.",
    "risk": "medium",
    "findings": [{"file": "app/util.py", "line": 5, "severity": "major", "category": "security",
                  "title": "shell=True with a caller-supplied command", "explanation": "Command injection.",
                  "suggestion": "subprocess.run(shlex.split(cmd), check=True)"}],
    "missing_tests": ["run() with a command containing ';'"],
    "changelog": "Changed: run() uses subprocess.",
}


class ClaudeReviewerTest(unittest.TestCase):
    def make(self, responses, **settings):
        client = FakeAnthropic(responses)
        return ClaudeReviewer(demo_settings(mode="live", **settings), client=client), client

    def test_request_uses_structured_outputs_caching_and_fallback(self):
        reviewer, client = self.make([fake_message(GOOD)])
        reviewer.review(SMALL_DIFF, title="Use subprocess")
        req = client.requests[0]
        self.assertEqual(req["model"], "claude-opus-5-5")
        self.assertEqual(req["system"][0]["text"], SYSTEM_PROMPT)
        self.assertEqual(req["system"][0]["cache_control"], {"type": "ephemeral"})
        self.assertEqual(req["output_config"]["format"], {"type": "json_schema", "schema": REVIEW_JSON_SCHEMA})
        self.assertEqual(req["output_config"]["effort"], "medium")
        self.assertEqual(req["fallbacks"], "default")
        self.assertNotIn("tool_choice", req)            # forced tool use is not accepted by Opus 5.5
        self.assertNotIn("temperature", req)

    def test_per_pr_content_is_after_the_cache_breakpoint(self):
        reviewer, client = self.make([fake_message(GOOD)])
        reviewer.review(SMALL_DIFF, title="Use subprocess", body="Closes #12")
        req = client.requests[0]
        self.assertNotIn("Use subprocess", req["system"][0]["text"])
        user = req["messages"][0]["content"]
        self.assertIn("<title>Use subprocess</title>", user)
        self.assertIn("    5 +    subprocess.run(cmd, shell=True)", user)
        self.assertIn("shell-true", user)                 # static hint passed to the model

    def test_result_usage_and_cost(self):
        reviewer, _ = self.make([fake_message(GOOD, input_tokens=1000, output_tokens=500, cache_write=2000)])
        out = reviewer.review(SMALL_DIFF)
        self.assertEqual(out.review.risk.value, "high")   # major security finding raises the floor
        self.assertEqual(out.usage.calls, 1)
        self.assertEqual(out.mode, "live")
        self.assertFalse(out.estimated)
        self.assertAlmostEqual(out.cost_usd, (1000 * 4 + 2000 * 5 + 500 * 20) / 1_000_000)

    def test_large_diff_is_reviewed_in_chunks_and_usage_is_summed(self):
        reviewer, client = self.make([fake_message({**GOOD, "findings": []}, cache_write=0, cache_read=800)],
                                     max_chunk_chars=1_200)
        out = reviewer.review(diff("clean-refactor.diff"))
        self.assertGreater(len(client.requests), 1)
        self.assertEqual(out.usage.calls, len(client.requests))
        self.assertEqual(out.usage.cache_read_tokens, 800 * len(client.requests))
        self.assertEqual(out.review.chunks, len(client.requests))

    def test_hallucinated_lines_are_moved_to_file_level(self):
        bad = {**GOOD, "findings": [{**GOOD["findings"][0], "line": 400}, {**GOOD["findings"][0], "file": "nope.py"}]}
        reviewer, _ = self.make([fake_message(bad)])
        out = reviewer.review(SMALL_DIFF)
        self.assertEqual([(f.file, f.line) for f in out.review.findings], [("app/util.py", 0)])

    def test_refusal_truncation_and_invalid_json_raise_review_error(self):
        for msg in (fake_message(GOOD, stop_reason="refusal"), fake_message(GOOD, stop_reason="max_tokens"),
                    fake_message("not json"), fake_message({"summary": "x"})):
            reviewer, _ = self.make([msg])
            with self.assertRaises(ReviewError):
                reviewer.review(SMALL_DIFF)

    def test_api_exception_is_wrapped(self):
        reviewer, _ = self.make([RuntimeError("connection reset")])
        with self.assertRaisesRegex(ReviewError, "connection reset"):
            reviewer.review(SMALL_DIFF)

    def test_no_fallback_for_models_without_it(self):
        reviewer, client = self.make([fake_message(GOOD)], model="claude-haiku-5-5")
        reviewer.review(SMALL_DIFF)
        self.assertNotIn("fallbacks", client.requests[0])


class DemoReviewerTest(unittest.TestCase):
    def test_every_sample_has_a_valid_grounded_canned_review(self):
        reviewer = DemoReviewer(demo_settings())
        for s in SAMPLES:
            files = parse_diff(s.diff())
            valid = {f.path: f.changed_new_lines() for f in files}
            canned = validate_chunk_review(s.review, valid)
            self.assertEqual(len(canned.findings), len(s.review["findings"]), s.id)   # nothing dropped
            out = reviewer.review(s.diff(), title=s.title)
            self.assertEqual(out.review.risk.value, s.review["risk"], s.id)
            self.assertTrue(out.estimated)

    def test_expected_risks_of_the_samples(self):
        risks = {s.id: s.review["risk"] for s in SAMPLES}
        self.assertEqual(risks["sql-injection"], "high")
        self.assertEqual(risks["leaked-secret"], "high")
        self.assertEqual(risks["clean-refactor"], "low")

    def test_fingerprint_ignores_line_endings_and_trailing_space(self):
        text = diff("n-plus-one-orders.diff")
        self.assertEqual(fingerprint(text), fingerprint(text.replace("\n", "\r\n") + "\n\n"))
        self.assertIsNotNone(find_sample(text.replace("\n", "\r\n")))

    def test_unknown_diff_gets_a_static_review(self):
        out = DemoReviewer(demo_settings()).review(SMALL_DIFF, title="Use subprocess")
        self.assertEqual([f.title for f in out.review.findings][0], "subprocess with shell=True")
        self.assertTrue(out.review.summary.startswith("[demo]"))
        self.assertEqual(out.usage.calls, 1)

    def test_demo_estimates_cache_write_then_reads(self):
        reviewer = DemoReviewer(demo_settings())
        first = reviewer.review(diff("clean-refactor.diff"))
        second = reviewer.review(diff("clean-refactor.diff"))
        self.assertGreater(first.usage.cache_write_tokens, 0)
        self.assertEqual(second.usage.cache_write_tokens, 0)
        self.assertGreater(second.usage.cache_read_tokens, 0)
        self.assertLess(second.cost_usd, first.cost_usd)

    def test_make_reviewer_picks_demo_without_key(self):
        self.assertIsInstance(make_reviewer(demo_settings()), DemoReviewer)


class HeuristicsTest(unittest.TestCase):
    def ids(self, name):
        return {(h.rule.id, h.line) for h in scan(parse_diff(diff(name)))}

    def test_sql_injection(self):
        self.assertIn(("sql-fstring", 29), self.ids("sql-injection-flask.diff"))

    def test_secrets_debug_and_cors(self):
        ids = {i for i, _ in self.ids("leaked-secret-config.diff")}
        self.assertTrue({"aws-access-key", "hardcoded-secret", "credentials-in-url", "debug-true", "cors-all"} <= ids)

    def test_n_plus_one(self):
        self.assertIn(("n-plus-one", 13), self.ids("n-plus-one-orders.diff"))

    def test_blocking_call_and_missing_timeout(self):
        ids = self.ids("async-blocking-call.diff")
        self.assertIn(("requests-in-async", 19), ids)
        self.assertIn(("no-timeout", 19), ids)

    def test_clean_refactor_has_no_hits_and_test_files_are_ignored(self):
        self.assertEqual(self.ids("clean-refactor.diff"), set())

    def test_hints_include_missing_tests(self):
        h = hints(parse_diff(diff("missing-tests-util.diff")))
        self.assertTrue(any("no test file" in x for x in h))


if __name__ == "__main__":
    unittest.main()
