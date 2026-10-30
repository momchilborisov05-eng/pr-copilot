import json
import unittest

from prcopilot.pricing import PRICES, Usage, cost_usd, cost_without_cache, estimate_tokens, price_for
from prcopilot.schema import (
    REVIEW_JSON_SCHEMA,
    ChunkReview,
    Finding,
    Risk,
    ValidationError,
    merge_reviews,
    risk_from_findings,
    validate_chunk_review,
)


def finding(**kw):
    base = dict(file="app.py", line=3, severity="minor", category="bug", title="Something off",
                explanation="Because reasons.", suggestion="Do it differently.")
    base.update(kw)
    return base


class SchemaTest(unittest.TestCase):
    def test_json_schema_is_strict_and_complete(self):
        self.assertFalse(REVIEW_JSON_SCHEMA["additionalProperties"])
        self.assertEqual(set(REVIEW_JSON_SCHEMA["required"]), set(REVIEW_JSON_SCHEMA["properties"]))
        item = REVIEW_JSON_SCHEMA["properties"]["findings"]["items"]
        self.assertFalse(item["additionalProperties"])
        self.assertEqual(item["properties"]["category"]["enum"], ["bug", "security", "performance", "style", "tests"])
        json.dumps(REVIEW_JSON_SCHEMA)   # serialisable for the API

    def test_valid_review_parses(self):
        r = validate_chunk_review({"summary": "Fine change.", "risk": "low", "findings": [finding()],
                                   "missing_tests": [], "changelog": "Fixed: x"})
        self.assertEqual(r.findings[0].severity.value, "minor")

    def test_rejects_unknown_category_and_negative_line(self):
        for bad in (finding(category="docs"), finding(line=-1), finding(severity="blocker")):
            with self.assertRaises(ValidationError):
                validate_chunk_review({"summary": "x" * 5, "risk": "low", "findings": [bad]})

    def test_rejects_missing_summary_and_bad_risk(self):
        with self.assertRaises(ValidationError):
            validate_chunk_review({"risk": "low", "findings": []})
        with self.assertRaises(ValidationError):
            validate_chunk_review({"summary": "ok ok", "risk": "extreme", "findings": []})

    def test_strips_git_prefix_from_paths(self):
        r = validate_chunk_review({"summary": "Fine.", "risk": "low", "findings": [finding(file="b/app.py")]})
        self.assertEqual(r.findings[0].file, "app.py")

    def test_grounding_drops_unknown_files_and_moves_bad_lines(self):
        data = {"summary": "Fine.", "risk": "low",
                "findings": [finding(file="ghost.py"), finding(line=99), finding(line=3)]}
        r = validate_chunk_review(data, {"app.py": {1, 2, 3}})
        self.assertEqual([(f.file, f.line) for f in r.findings], [("app.py", 0), ("app.py", 3)])

    def test_risk_is_never_lower_than_the_worst_finding(self):
        r = validate_chunk_review({"summary": "Looks ok.", "risk": "low",
                                   "findings": [finding(severity="major", category="security")]})
        self.assertEqual(r.risk, Risk.high)

    def test_risk_from_findings(self):
        mk = lambda **kw: Finding(**finding(**kw))  # noqa: E731
        self.assertEqual(risk_from_findings([]), Risk.low)
        self.assertEqual(risk_from_findings([mk(severity="major")]), Risk.medium)
        self.assertEqual(risk_from_findings([mk(severity="critical")]), Risk.high)
        self.assertEqual(risk_from_findings([mk(severity="minor"), mk(severity="info")]), Risk.low)

    def test_merge_takes_worst_risk_dedupes_and_sorts(self):
        a = ChunkReview(summary="Part one.", risk="low", findings=[finding(), finding(severity="critical", line=9)],
                        missing_tests=["t1"], changelog="Added: a")
        b = ChunkReview(summary="Part two.", risk="medium", findings=[finding()], missing_tests=["t1", "t2"])
        m = merge_reviews([a, b], ["app.py"], ["x.lock"])
        self.assertEqual(m.risk, Risk.medium)
        self.assertEqual([f.severity.value for f in m.findings], ["critical", "minor"])
        self.assertEqual(m.missing_tests, ["t1", "t2"])
        self.assertEqual((m.chunks, m.changelog, m.files_skipped), (2, "Added: a", ["x.lock"]))

    def test_merge_of_nothing(self):
        m = merge_reviews([], [], ["logo.png"])
        self.assertEqual((m.risk, m.chunks, m.findings), (Risk.low, 0, []))


class PricingTest(unittest.TestCase):
    def test_default_model_price(self):
        p, known = price_for("claude-opus-5-5")
        self.assertTrue(known)
        self.assertEqual((p["input"], p["output"], p["cache_read"]), (4.0, 20.0, 0.20))

    def test_cost_formula_includes_cache_write_premium_and_cheap_reads(self):
        u = Usage(input_tokens=1_000_000, output_tokens=1_000_000, cache_read_tokens=1_000_000, cache_write_tokens=1_000_000)
        # 4 input + 20 output + 0.20 cache read + 4 * 1.25 cache write
        self.assertAlmostEqual(cost_usd(u, "claude-opus-5-5"), 29.20)

    def test_cache_saving_is_positive_with_reads(self):
        u = Usage(input_tokens=500, output_tokens=300, cache_read_tokens=4000)
        self.assertGreater(cost_without_cache(u, "claude-opus-5-5"), cost_usd(u, "claude-opus-5-5"))

    def test_unknown_model_falls_back_and_is_flagged(self):
        p, known = price_for("some-future-model")
        self.assertFalse(known)
        self.assertEqual(p, PRICES["claude-opus-5-5"])

    def test_provider_prefix_is_stripped(self):
        self.assertTrue(price_for("anthropic.claude-opus-5-5")[1])

    def test_usage_addition_and_hit_rate(self):
        total = Usage(100, 10, 900, 0, 1) + Usage(50, 5, 0, 100, 1)
        self.assertEqual((total.input_tokens, total.calls, total.total_input), (150, 2, 1150))
        self.assertAlmostEqual(total.cache_hit_rate, 900 / 1150)
        self.assertEqual(Usage().cache_hit_rate, 0.0)

    def test_estimate_tokens(self):
        self.assertEqual(estimate_tokens("a" * 400), 100)
        self.assertEqual(estimate_tokens(""), 1)


if __name__ == "__main__":
    unittest.main()
