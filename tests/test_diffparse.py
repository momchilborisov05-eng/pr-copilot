import unittest

from prcopilot.diffparse import (
    chunk_files,
    diff_stats,
    filter_files,
    is_test_path,
    missing_test_candidates,
    parse_diff,
)
from tests.helpers import SMALL_DIFF, diff


class ParseDiffTest(unittest.TestCase):
    def test_parses_files_and_counts(self):
        files = parse_diff(diff("clean-refactor.diff"))
        self.assertEqual([f.path for f in files], ["accounts/validators.py", "accounts/forms.py", "tests/test_validators.py"])
        self.assertEqual(files[0].status, "added")
        self.assertEqual((files[1].additions, files[1].deletions), (4, 12))

    def test_new_file_line_numbers_follow_hunk_header(self):
        [f] = parse_diff(diff("sql-injection-flask.diff"))
        added = {ln.new_no: ln.text for ln in f.added_lines()}
        self.assertIn("ORDER BY {sort}", added[29])
        self.assertEqual(added[10], "@login_required")

    def test_context_lines_have_both_numbers(self):
        files = parse_diff(diff("n-plus-one-orders.diff"))
        test_file = files[1]
        first = test_file.hunks[0].lines[0]
        self.assertEqual((first.kind, first.old_no, first.new_no), (" ", 10, 10))

    def test_changed_new_lines_excludes_removed_only_positions(self):
        [f] = parse_diff(SMALL_DIFF)
        self.assertEqual(f.changed_new_lines(), {1, 2, 3, 4, 5, 6})

    def test_tolerates_crlf_and_stripped_blank_context(self):
        text = SMALL_DIFF.replace("\n \n", "\n\n").replace("\n", "\r\n")
        [f] = parse_diff(text)
        self.assertEqual((f.additions, f.deletions), (3, 1))
        self.assertEqual(max(f.changed_new_lines()), 6)

    def test_plain_unified_diff_without_git_header(self):
        text = "--- a/x.py\n+++ b/x.py\n@@ -1 +1,2 @@\n a = 1\n+b = 2\n"
        [f] = parse_diff(text)
        self.assertEqual((f.path, f.additions), ("x.py", 1))

    def test_deleted_renamed_and_binary_files(self):
        text = (
            "diff --git a/old.py b/old.py\ndeleted file mode 100644\n--- a/old.py\n+++ /dev/null\n@@ -1 +0,0 @@\n-x = 1\n"
            "diff --git a/a.py b/b.py\nsimilarity index 100%\nrename from a.py\nrename to b.py\n"
            "diff --git a/logo.png b/logo.png\nBinary files a/logo.png and b/logo.png differ\n"
        )
        files = parse_diff(text)
        self.assertEqual([f.status for f in files], ["deleted", "renamed", "modified"])
        self.assertEqual(files[1].path, "b.py")
        self.assertTrue(files[2].is_binary)

    def test_removed_line_that_looks_like_a_header_stays_in_the_hunk(self):
        text = "diff --git a/n.md b/n.md\n--- a/n.md\n+++ b/n.md\n@@ -1 +1 @@\n--- old note\n+++ new note\n"
        [f] = parse_diff(text)
        self.assertEqual((f.additions, f.deletions), (1, 1))

    def test_language_detection(self):
        files = parse_diff(diff("leaked-secret-config.diff"))
        self.assertEqual([f.language for f in files], ["python", "dotenv"])


class FilterAndChunkTest(unittest.TestCase):
    def test_filter_skips_lockfiles_binary_and_deleted(self):
        text = (
            "diff --git a/poetry.lock b/poetry.lock\n--- a/poetry.lock\n+++ b/poetry.lock\n@@ -1 +1 @@\n-a\n+b\n"
            + SMALL_DIFF
        )
        kept, skipped = filter_files(parse_diff(text), ("*.lock",))
        self.assertEqual([f.path for f in kept], ["app/util.py"])
        self.assertEqual(skipped, ["poetry.lock"])

    def test_small_files_share_one_chunk(self):
        files = parse_diff(diff("clean-refactor.diff"))
        chunks = chunk_files(files, 24_000)
        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0].paths, [f.path for f in files])

    def test_files_split_into_several_chunks_when_budget_is_small(self):
        files = parse_diff(diff("clean-refactor.diff"))
        chunks = chunk_files(files, 1_200)
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(len(c) <= 1_200 or len(c.parts) == 1 for c in chunks))
        covered = [p for c in chunks for p in c.paths]
        self.assertEqual(sorted(set(covered)), sorted(f.path for f in files))

    def test_large_file_is_split_on_hunk_boundaries(self):
        hunks = "".join(
            f"@@ -{i * 10 + 1},1 +{i * 10 + 1},2 @@\n ctx{i}\n+" + ("x" * 300) + "\n" for i in range(6)
        )
        text = "diff --git a/big.py b/big.py\n--- a/big.py\n+++ b/big.py\n" + hunks
        [f] = parse_diff(text)
        chunks = chunk_files([f], 800)
        self.assertGreater(len(chunks), 2)
        self.assertEqual(sum(len(h) for c in chunks for _, h in c.parts), 6)   # every hunk exactly once

    def test_chunk_render_contains_line_numbers(self):
        [chunk] = chunk_files(parse_diff(diff("sql-injection-flask.diff")), 24_000)
        self.assertIn("   29 +", chunk.render())

    def test_rejects_tiny_budget(self):
        with self.assertRaises(ValueError):
            chunk_files(parse_diff(SMALL_DIFF), 50)


class TestDetectionTest(unittest.TestCase):
    def test_is_test_path(self):
        for p in ("tests/test_x.py", "app/tests/foo.py", "src/__tests__/a.js", "pkg/x_test.go", "web/a.spec.ts"):
            self.assertTrue(is_test_path(p), p)
        self.assertFalse(is_test_path("app/testing_utils.py"))

    def test_missing_tests_when_no_test_file_changed(self):
        self.assertEqual(missing_test_candidates(parse_diff(diff("missing-tests-util.diff"))),
                         ["shop/utils/text.py", "shop/views/catalogue.py"])

    def test_no_missing_tests_when_tests_are_touched(self):
        self.assertEqual(missing_test_candidates(parse_diff(diff("clean-refactor.diff"))), [])

    def test_diff_stats(self):
        self.assertEqual(diff_stats(parse_diff(diff("async-blocking-call.diff"))), {"files": 1, "additions": 19, "deletions": 3})


if __name__ == "__main__":
    unittest.main()
