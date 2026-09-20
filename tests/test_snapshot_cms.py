"""The CMS snapshotter, driven with a fake fetcher so no network is needed.

The properties that matter are all about not losing a month:

* an unchanged file must not create a second copy, or the archive becomes
  noise and nobody can tell which months actually differed;
* a file that could not be fetched must be reported as failed, never as
  unchanged - this workstation has now recorded five instruments that
  reported "could not look" as "found nothing";
* a failed fetch must not damage what is already archived, because the point
  of the archive is that CMS cannot give it back.
"""

import datetime
import gzip
import json
import os
import shutil
import sys
import tempfile
import unittest

import os.path as _p
# The engine sits in tools/ beside this directory. Resolved from __file__
# rather than from ".", because these tests used to live in the
# workstation's tools/ and be run with that as the working directory -
# which made them unrunnable from the repository that owns them.
sys.path.insert(0, _p.join(_p.dirname(_p.dirname(_p.abspath(__file__))), "tools"))
import snapshot_cms               # with PYTHONPATH=. - see tools/README.md


def catalog(identifier, url):
    return json.dumps({
        "title": identifier.upper(), "modified": "2026-08-01",
        "distribution": [{"data": {"downloadURL": url}}],
    }).encode("utf-8")


class Fetcher(object):
    """Serves catalog JSON for any identifier and a payload for any file."""

    def __init__(self, payload=b"a,b\n1,2\n", fail_on=None):
        self.payload = payload
        self.fail_on = fail_on or set()
        self.seen = []

    def __call__(self, url):
        self.seen.append(url)
        for bad in self.fail_on:
            if bad in url:
                raise OSError("simulated failure for %s" % bad)
        if "/metastore/" in url:
            ident = url.rsplit("/", 1)[-1].split("?")[0]
            return catalog(ident, "https://example.test/%s.csv" % ident), {}
        return self.payload, {}


class SnapshotTests(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._real = snapshot_cms.ARCHIVE
        snapshot_cms.ARCHIVE = os.path.join(self.tmp, "cms")

    def tearDown(self):
        snapshot_cms.ARCHIVE = self._real
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_once(self, fetcher, day, **kw):
        return snapshot_cms.run(today=datetime.date.fromisoformat(day),
                                get=fetcher, sizer=lambda u: 123, **kw)

    # -- idempotence ------------------------------------------------------

    def test_the_first_run_archives_and_writes_a_dated_directory(self):
        rows, wrote = self.run_once(Fetcher(), "2026-09-18")
        self.assertTrue(wrote.endswith("2026-09-18"))
        archived = [r for r in rows if r["status"] == "new"]
        self.assertEqual(len(archived), 3)
        for row in archived:
            self.assertTrue(os.path.isfile(
                os.path.join(wrote, "%s.csv.gz" % row["name"])))

    def test_an_unchanged_month_writes_nothing(self):
        self.run_once(Fetcher(), "2026-09-18")
        rows, wrote = self.run_once(Fetcher(), "2026-10-18")
        self.assertIsNone(wrote)
        self.assertFalse(os.path.exists(
            os.path.join(snapshot_cms.ARCHIVE, "2026-10-18")))
        for row in rows:
            if row["archived"]:
                self.assertEqual(row["status"], "unchanged", row["name"])

    def test_a_changed_file_writes_a_new_capture(self):
        self.run_once(Fetcher(), "2026-09-18")
        rows, wrote = self.run_once(Fetcher(payload=b"a,b\n9,9\n"), "2026-10-18")
        self.assertTrue(wrote.endswith("2026-10-18"))
        self.assertTrue(any(r["status"] == "new" for r in rows))

    def test_dry_run_writes_nothing_at_all(self):
        rows, wrote = self.run_once(Fetcher(), "2026-09-18", dry_run=True)
        self.assertIsNone(wrote)
        self.assertFalse(os.path.exists(snapshot_cms.ARCHIVE))
        self.assertTrue(any(r["status"] == "new" for r in rows))

    # -- could not look is not found nothing ------------------------------

    def test_a_failed_fetch_is_reported_failed_not_unchanged(self):
        rows, _ = self.run_once(Fetcher(fail_on={"g6vv-u9sr.csv"}), "2026-09-18")
        penalties = [r for r in rows if r["name"] == "penalties"][0]
        self.assertEqual(penalties["status"], "fetch_failed")
        self.assertNotEqual(penalties["status"], "unchanged")

    def test_an_unresolvable_dataset_is_reported_failed(self):
        rows, _ = self.run_once(Fetcher(fail_on={"metastore"}), "2026-09-18")
        self.assertTrue(rows)
        for row in rows:
            self.assertEqual(row["status"], "unresolved", row["name"])

    def test_the_report_says_a_failure_is_not_unchanged(self):
        rows, wrote = self.run_once(Fetcher(fail_on={"g6vv-u9sr.csv"}),
                                    "2026-09-18")
        text = snapshot_cms.report(rows, wrote)
        self.assertIn("COULD NOT BE FETCHED", text)
        self.assertIn("not 'unchanged'", text)

    def test_a_failed_fetch_does_not_damage_the_existing_archive(self):
        _, first = self.run_once(Fetcher(), "2026-09-18")
        before = sorted(os.listdir(first))
        self.run_once(Fetcher(fail_on={"g6vv-u9sr.csv"}), "2026-10-18")
        self.assertEqual(sorted(os.listdir(first)), before)
        with gzip.open(os.path.join(first, "penalties.csv.gz"), "rt") as handle:
            self.assertEqual(handle.read(), "a,b\n1,2\n")

    def test_a_partial_failure_still_archives_what_it_could_get(self):
        """A bad month for one file must not cost the other four."""
        rows, wrote = self.run_once(Fetcher(fail_on={"g6vv-u9sr.csv"}),
                                    "2026-09-18")
        self.assertIsNotNone(wrote)
        self.assertTrue(os.path.isfile(
            os.path.join(wrote, "survey_summary.csv.gz")))
        self.assertFalse(os.path.isfile(
            os.path.join(wrote, "penalties.csv.gz")))

    # -- the files not archived are recorded, not dropped -----------------

    def test_the_oversized_files_appear_in_the_manifest(self):
        _, wrote = self.run_once(Fetcher(), "2026-09-18")
        with open(os.path.join(wrote, "manifest.json"), encoding="utf-8") as f:
            manifest = json.load(f)
        names = {r["name"]: r for r in manifest["files"]}
        for big in ("health_deficiencies", "fire_safety_deficiencies"):
            self.assertIn(big, names)
            self.assertEqual(names[big]["status"], "manifest_only")
            self.assertTrue(names[big]["url"], "the URL must be kept")

    def test_the_report_names_the_gap_rather_than_hiding_it(self):
        rows, wrote = self.run_once(Fetcher(), "2026-09-18")
        text = snapshot_cms.report(rows, wrote)
        self.assertIn("not archived by design", text)

    # -- the URL must be re-resolved every run ----------------------------

    def test_the_download_url_is_resolved_not_hardcoded(self):
        """It embeds a content hash and build timestamp that change monthly."""
        fetcher = Fetcher()
        self.run_once(fetcher, "2026-09-18")
        catalog_calls = [u for u in fetcher.seen if "/metastore/" in u]
        self.assertEqual(len(catalog_calls), len(snapshot_cms.DATASETS))
        for identifier in snapshot_cms.DATASETS:
            self.assertTrue(any(identifier in u for u in catalog_calls),
                            "%s was never resolved" % identifier)

    def test_a_catalog_without_a_download_url_raises(self):
        def no_url(url):
            return json.dumps({"title": "x", "distribution": []}).encode(), {}
        with self.assertRaises(RuntimeError):
            snapshot_cms.resolve("g6vv-u9sr", get=no_url)

    # -- the archive must be readable back --------------------------------

    def test_an_archived_file_round_trips(self):
        payload = b"CCN,Fine\n015019,182968\n"
        _, wrote = self.run_once(Fetcher(payload=payload), "2026-09-18")
        with gzip.open(os.path.join(wrote, "penalties.csv.gz"), "rb") as handle:
            self.assertEqual(handle.read(), payload)

    def test_the_manifest_records_a_digest_and_a_row_count(self):
        _, wrote = self.run_once(Fetcher(payload=b"a,b\n1,2\n3,4\n"),
                                 "2026-09-18")
        with open(os.path.join(wrote, "manifest.json"), encoding="utf-8") as f:
            rows = {r["name"]: r for r in json.load(f)["files"]}
        self.assertEqual(rows["penalties"]["rows"], 2)
        self.assertEqual(len(rows["penalties"]["sha256"]), 64)

    def test_no_payload_bytes_leak_into_the_manifest(self):
        _, wrote = self.run_once(Fetcher(), "2026-09-18")
        with open(os.path.join(wrote, "manifest.json"), encoding="utf-8") as f:
            text = f.read()
        self.assertNotIn("_body", text)


if __name__ == "__main__":
    unittest.main()
