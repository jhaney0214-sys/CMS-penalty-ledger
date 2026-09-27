"""The unattended capture's git step, driven with a fake git.

What matters is that it runs with nobody watching:

* it commits the capture directory and nothing else, whatever the author
  happened to have staged when the task fired;
* it refuses to commit off main rather than put a capture on a stray branch;
* a failed push is reported as "this disk only", never as success.
"""

import os
import shutil
import sys
import tempfile
import unittest

import os.path as _p
sys.path.insert(0, _p.join(_p.dirname(_p.dirname(_p.abspath(__file__))), "tools"))
import cms_scheduled              # noqa: E402

CAPTURE = os.path.join(cms_scheduled.HERE, "snapshots", "cms", "2026-10-02")


class FakeGit(object):
    def __init__(self, branch="main", fail=None):
        self.branch, self.fail, self.calls = branch, fail, []

    def __call__(self, *args):
        self.calls.append(args)
        if args[0] == "rev-parse":
            return 0, self.branch
        if args[0] == self.fail:
            return 1, "%s: refused" % args[0]
        return 0, ""


class TestCommitAndPush(unittest.TestCase):

    def test_commits_only_the_capture_directory(self):
        git = FakeGit()
        lines = cms_scheduled.commit_and_push(CAPTURE, git=git)
        commit = [c for c in git.calls if c[0] == "commit"][0]
        self.assertEqual(commit[-2:], ("--", "snapshots/cms/2026-10-02"))
        add = [c for c in git.calls if c[0] == "add"][0]
        self.assertEqual(add[-1], "snapshots/cms/2026-10-02")
        self.assertIn("committed and pushed", lines[0])

    def test_refuses_off_main(self):
        git = FakeGit(branch="experiment")
        lines = cms_scheduled.commit_and_push(CAPTURE, git=git)
        self.assertIn("NOT COMMITTED", lines[0])
        self.assertFalse([c for c in git.calls if c[0] in ("add", "commit")])

    def test_failed_push_is_not_reported_as_success(self):
        git = FakeGit(fail="push")
        lines = " ".join(cms_scheduled.commit_and_push(CAPTURE, git=git))
        self.assertIn("PUSH FAILED", lines)
        self.assertIn("this disk only", lines)
        self.assertNotIn("committed and pushed", lines)

    def test_failed_commit_does_not_push(self):
        git = FakeGit(fail="commit")
        lines = cms_scheduled.commit_and_push(CAPTURE, git=git)
        self.assertIn("NOT COMMITTED", lines[0])
        self.assertFalse([c for c in git.calls if c[0] == "push"])


class TestRepublish(unittest.TestCase):
    """After a new capture, the page's data follows it."""

    def setUp(self):
        self.root = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.root, ".git"))

    def tearDown(self):
        shutil.rmtree(self.root)

    def test_exports_then_commits_only_docs_data(self):
        seen = []
        git = FakeGit()
        lines = cms_scheduled.republish(
            self.root, export=lambda out: seen.append(out) or
            {"capture": "2026-10-02"}, git=git)
        self.assertEqual(seen, [os.path.join(self.root, "docs", "data")])
        commit = [c for c in git.calls if c[0] == "commit"][0]
        self.assertEqual(commit[-2:], ("--", "docs/data"))
        self.assertIn("2026-10-02", commit[2])
        self.assertIn("committed and pushed", lines[0])

    def test_a_failed_export_commits_nothing(self):
        def broken(out):
            raise ValueError("bad capture")
        git = FakeGit()
        lines = cms_scheduled.republish(self.root, export=broken, git=git)
        self.assertIn("NOT refreshed", lines[0])
        self.assertEqual(git.calls, [])

    def test_no_ledger_repository_is_said_not_skipped(self):
        lines = cms_scheduled.republish(os.path.join(self.root, "absent"))
        self.assertIn("not refreshed", lines[0])


class TestLiveCheck(unittest.TestCase):
    """The page is uploaded by hand, so a pushed capture can leave it behind."""

    def setUp(self):
        self.root = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.root, "docs", "data"))
        with open(os.path.join(self.root, "docs", "data", "meta.json"), "w") as handle:
            handle.write('{"capture": "2026-10-02"}')

    def tearDown(self):
        shutil.rmtree(self.root)

    def test_current_page_passes(self):
        behind, line = cms_scheduled.live_check(self.root, fetch=lambda u: '{"capture": "2026-10-02"}')
        self.assertFalse(behind)
        self.assertIn("current", line)

    def test_behind_page_is_said_loudly(self):
        behind, line = cms_scheduled.live_check(self.root, fetch=lambda u: '{"capture": "2026-09-18"}')
        self.assertTrue(behind)
        self.assertIn("LIVE PAGE BEHIND", line)
        self.assertIn("2026-09-18", line)

    def test_an_unreachable_page_is_not_checked_not_current(self):
        def down(url):
            raise OSError("offline")
        behind, line = cms_scheduled.live_check(self.root, fetch=down)
        self.assertTrue(behind)
        self.assertIn("NOT CHECKED", line)


if __name__ == "__main__":
    unittest.main()
