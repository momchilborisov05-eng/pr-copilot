import io
import json
import tempfile
import unittest
from pathlib import Path

import yaml

from prcopilot.action import annotations, main
from prcopilot.github import FakeGitHubClient, GitHubError
from prcopilot.reviewer import DemoReviewer
from tests.helpers import DIFFS, ROOT, demo_settings, diff


class ActionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.output = self.dir / "out.txt"
        self.summary = self.dir / "summary.md"
        event = {"pull_request": {"number": 7, "title": "Add search", "body": "", "head": {"sha": "deadbeef"}}}
        (self.dir / "event.json").write_text(json.dumps(event), encoding="utf-8")
        self.env = {"GITHUB_EVENT_PATH": str(self.dir / "event.json"), "GITHUB_REPOSITORY": "acme/support-api",
                    "GITHUB_OUTPUT": str(self.output), "GITHUB_STEP_SUMMARY": str(self.summary), "PRCOPILOT_MODE": "demo"}
        self.github = FakeGitHubClient(diffs={("acme/support-api", 7): diff("sql-injection-flask.diff")})

    def tearDown(self):
        self.tmp.cleanup()

    def run_action(self, *args, env=None, github=None):
        out = io.StringIO()
        code = main(list(args), env=env or self.env, reviewer=DemoReviewer(demo_settings()),
                    github=github or self.github, stdout=out)
        return code, out.getvalue()

    def outputs(self):
        return dict(line.split("=", 1) for line in self.output.read_text(encoding="utf-8").splitlines())

    def test_reviews_pr_from_event_and_posts(self):
        code, out = self.run_action()
        self.assertEqual(code, 0)
        self.assertEqual(self.outputs()["risk"], "high")
        self.assertEqual(self.outputs()["mode"], "demo")
        self.assertIn("PR Copilot review", self.summary.read_text(encoding="utf-8"))
        self.assertIn("::error file=app/routes/customers.py,line=29", out)
        [post] = self.github.posted
        self.assertEqual((post["number"], post["commit_id"], post["event"]), (7, "deadbeef", "COMMENT"))

    def test_fail_on_gate(self):
        code, out = self.run_action("--fail-on", "high")
        self.assertEqual(code, 1)
        self.assertIn("reaches the fail-on threshold", out)
        self.assertEqual(self.github.posted[0]["event"], "REQUEST_CHANGES")

    def test_diff_file_and_no_post(self):
        code, _ = self.run_action("--diff-file", str(DIFFS / "clean-refactor.diff"), "--no-post", "--fail-on", "medium")
        self.assertEqual(code, 0)
        self.assertEqual(self.outputs()["risk"], "low")
        self.assertEqual(self.github.posted, [])

    def test_json_out(self):
        target = self.dir / "review.json"
        self.run_action("--json-out", str(target), "--no-post")
        data = json.loads(target.read_text(encoding="utf-8"))
        self.assertEqual(data["review"]["risk"], "high")
        self.assertIn("cache_read_tokens", data["usage"])

    def test_no_diff_source_is_an_error(self):
        code, out = self.run_action(env={"PRCOPILOT_MODE": "demo"})
        self.assertEqual(code, 2)
        self.assertIn("No diff", out)

    def test_posting_failure_is_a_warning_not_a_failure(self):
        class ReadOnly(FakeGitHubClient):
            def post_review(self, *a, **kw):
                raise GitHubError("HTTP 403")

        gh = ReadOnly(diffs={("acme/support-api", 7): diff("clean-refactor.diff")})
        code, out = self.run_action(github=gh)
        self.assertEqual(code, 0)
        self.assertIn("::warning", out)

    def test_annotation_escaping(self):
        from prcopilot.schema import Review

        r = Review(summary="x x x", risk="low", findings=[dict(
            file="a.py", line=0, severity="minor", category="bug", title="100% wrong", explanation="e e",
            suggestion="line1\nline2")])
        [line] = annotations(r)
        self.assertIn("100%25 wrong", line)
        self.assertIn("line1%0Aline2", line)
        self.assertNotIn(",line=", line)


class ProjectFilesTest(unittest.TestCase):
    """The CI/CD and container files are part of the deliverable, so keep them valid."""

    def load(self, rel):
        return yaml.safe_load((ROOT / rel).read_text(encoding="utf-8"))

    def test_action_yml_is_a_composite_action_calling_the_entry_point(self):
        action = self.load("action.yml")
        self.assertEqual(action["runs"]["using"], "composite")
        self.assertIn("fail-on", action["inputs"])
        self.assertTrue(any("prcopilot.action" in s.get("run", "") for s in action["runs"]["steps"]))
        self.assertIn("risk", action["outputs"])

    def test_ci_workflow_has_lint_test_build_and_deploy(self):
        ci = self.load(".github/workflows/ci.yml")
        self.assertTrue({"lint", "test", "docker", "deploy"} <= set(ci["jobs"]))
        self.assertIn("main", str(ci["jobs"]["deploy"].get("if", "")))
        self.assertIn("packages", ci["jobs"]["deploy"]["permissions"])

    def test_pr_review_workflow_uses_the_local_action(self):
        wf = self.load(".github/workflows/pr-review.yml")
        steps = [s for job in wf["jobs"].values() for s in job["steps"]]
        self.assertTrue(any(s.get("uses") == "./" for s in steps))
        self.assertIn("pull_request", wf[True] if True in wf else wf["on"])   # PyYAML reads `on:` as True

    def test_dockerfile_is_multi_stage_non_root_with_healthcheck(self):
        text = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        self.assertGreaterEqual(text.count("FROM "), 2)
        self.assertIn("USER app", text)
        self.assertIn("HEALTHCHECK", text)

    def test_compose_file(self):
        compose = self.load("docker-compose.yml")
        svc = compose["services"]["pr-copilot"]
        self.assertIn("8000:8000", svc["ports"][0])
        self.assertIn("healthcheck", svc)


if __name__ == "__main__":
    unittest.main()
