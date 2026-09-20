"""The exporter behind the Nursing Home Penalty Ledger page, run against synthetic captures.

The properties that matter:

* its numbers are cms_ledger's numbers - the page must not disagree with the
  command line, so every total is checked against cms_ledger computing it;
* fines and denials stay separate, and a denial never enters a dollar total;
* a penalty an earlier capture had and the latest does not is exported as
  dropped, with the capture it was last seen in - the archive's whole point;
* with one capture there is nothing to compare, and the data says how many
  captures were compared so the page cannot print "none dropped".
"""

import csv
import gzip
import io
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
import cms_export                 # noqa: E402
import cms_ledger                 # noqa: E402

PEN_COLS = ["CMS Certification Number (CCN)", "Provider Name",
            "Provider Address", "City/Town", "State", "ZIP Code",
            "Penalty Date", "Penalty Type", "Fine ID", "Fine Amount",
            "Payment Denial Start Date", "Payment Denial Length in Days",
            "Processing Date"]
SUR_COLS = ["CMS Certification Number (CCN)", "Provider Name", "City/Town",
            "State", "Inspection Cycle", "Health Survey Date",
            "Fire Safety Survey Date", "Total Number of Health Deficiencies",
            "Total Number of Fire Safety Deficiencies",
            "Count of Freedom from Abuse and Neglect and Exploitation "
            "Deficiencies", "Count of Infection Control Deficiencies"]


def fine(ccn, fid, amount, date="2025-01-01", state="AL", name="A HOME"):
    return [ccn, name, "1 ST", "TOWN", state, "35000", date, "Fine", fid,
            str(amount), "", "", "2026-08-01"]


def denial(ccn, days, date="2025-02-01", state="AL", name="A HOME"):
    return [ccn, name, "1 ST", "TOWN", state, "35000", date,
            "Payment Denial", "", "", "2025-03-01", str(days), "2026-08-01"]


def survey(ccn, state="AL", name="A HOME", cycle="1"):
    return [ccn, name, "TOWN", state, cycle, "2025-01-01", "2025-01-01",
            "4", "2", "1", "0"]


def write_capture(root, stamp, penalties, surveys):
    path = os.path.join(root, stamp)
    os.makedirs(path)
    for name, cols, rows in (("penalties", PEN_COLS, penalties),
                             ("survey_summary", SUR_COLS, surveys)):
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(cols)
        w.writerows(rows)
        with gzip.open(os.path.join(path, name + ".csv.gz"), "wt",
                       encoding="utf-8") as f:
            f.write(buf.getvalue())
    with open(os.path.join(path, "manifest.json"), "w") as f:
        json.dump({"files": [{"title": "Health Deficiencies",
                              "archived": False}]}, f)


class ExportTest(unittest.TestCase):

    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.archive = os.path.join(self.root, "cms")
        self.out = os.path.join(self.root, "out")

    def tearDown(self):
        shutil.rmtree(self.root)

    def read(self, *parts):
        with open(os.path.join(self.out, *parts), encoding="utf-8") as f:
            return json.load(f)


class TestOneCapture(ExportTest):

    def setUp(self):
        super().setUp()
        write_capture(self.archive, "2026-09-18",
                      [fine("015001", "F1", 1000), fine("015001", "F2", 500),
                       denial("015001", 30), fine("055001", "F3", 700,
                                                  state="CA", name="C HOME")],
                      [survey("015001"), survey("015002", name="B HOME"),
                       survey("055001", state="CA", name="C HOME")])
        self.meta = cms_export.export(self.out, archive=self.archive)

    def test_totals_are_cms_ledgers(self):
        capture = cms_ledger.load(archive=self.archive)
        s = cms_ledger.state_summary(capture, "AL")
        al = self.meta["states"]["AL"]
        self.assertEqual((al["surveyed"], al["penalised"], al["not_penalised"],
                          al["fines"], al["fine_total"], al["denials"]),
                         (s["surveyed"], s["penalised"], s["clean"],
                          len(s["fines"]), s["fine_total"], len(s["denials"])))
        self.assertEqual(self.meta["fine_total"], 2200)

    def test_a_denial_never_enters_a_dollar_total(self):
        rec = self.read("states", "AL.json")["015001"]
        self.assertEqual(sorted(f[1] for f in rec["fines"]), [500, 1000])
        self.assertEqual(rec["denials"], [["2025-02-01", "2025-03-01", 30]])
        self.assertEqual(self.meta["states"]["AL"]["fine_total"], 1500)

    def test_an_unpenalised_facility_is_listed_not_dropped(self):
        index = {row[0]: row for row in self.read("index.json")}
        self.assertEqual(index["015002"][4], 0)
        self.assertEqual(index["015001"][4], 1)

    def test_one_capture_means_nothing_compared(self):
        self.assertEqual(self.meta["captures"], ["2026-09-18"])
        self.assertEqual(self.meta["dropped"], 0)

    def test_the_refusals_are_cms_ledgers_words(self):
        self.assertEqual(self.meta["text"]["no_penalty"], cms_ledger.NO_PENALTY)
        self.assertEqual(self.meta["not_kept"], ["Health Deficiencies"])


class TestTwoCaptures(ExportTest):

    def setUp(self):
        super().setUp()
        write_capture(self.archive, "2026-09-18",
                      [fine("015001", "F1", 1000), fine("015001", "OLD", 250,
                                                        date="2023-08-20"),
                       denial("015001", 30, date="2023-08-21")],
                      [survey("015001")])
        write_capture(self.archive, "2026-10-02",
                      [fine("015001", "F1", 1000)], [survey("015001")])
        self.meta = cms_export.export(self.out, archive=self.archive)

    def test_dropped_penalties_are_kept_with_last_seen(self):
        rec = self.read("states", "AL.json")["015001"]
        self.assertEqual(rec["fines"], [["2025-01-01", 1000]])
        self.assertEqual(sorted(rec["dropped"]), [
            ["Fine", "2023-08-20", 250, "2026-09-18"],
            ["Payment Denial", "2023-08-21", 30, "2026-09-18"]])
        self.assertEqual(self.meta["dropped"], 2)
        self.assertEqual(self.meta["captures"], ["2026-09-18", "2026-10-02"])

    def test_the_latest_capture_is_the_one_reported(self):
        self.assertEqual(self.meta["capture"], "2026-10-02")
        self.assertEqual(self.meta["fine_total"], 1000)


class TestRebuiltHistory(ExportTest):
    """`backfill_cms.py`'s history, beside the archive: penalties dropped
    before the first capture reach the page as dropped."""

    HEADER = ["ccn", "name", "address", "city", "state", "zip", "date", "kind",
              "fine_id", "amount", "denial_start", "denial_days", "first_seen",
              "last_seen", "appearances", "gaps", "in_latest"]

    def setUp(self):
        super().setUp()
        write_capture(self.archive, "2026-09-18",
                      [fine("015001", "F1", 1000)], [survey("015001")])
        hist = os.path.join(self.root, "cms-archive")
        os.makedirs(hist)
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(self.HEADER)
        w.writerow(["015001", "A HOME", "1 A", "X", "AL", "1", "2025-01-01",
                    "Fine", "F1", "1000", "", "", "2025-02-26", "2026-08-26",
                    "18", "0", "yes"])
        w.writerow(["015001", "A HOME", "1 A", "X", "AL", "1", "2017-03-02",
                    "Fine", "", "15259", "", "", "2019-01-17", "2020-05-09",
                    "16", "0", "no"])
        w.writerow(["015099", "CLOSED HOME", "1 C", "Y", "AL", "1", "2018-06-01",
                    "Payment Denial", "", "", "2018-07-01", "40", "2019-01-17",
                    "2021-09-27", "30", "0", "no"])
        with gzip.open(os.path.join(hist, "penalties_history.csv.gz"), "wt",
                       encoding="utf-8") as f:
            f.write(buf.getvalue())
        with open(os.path.join(hist, "manifest.json"), "w") as f:
            json.dump({"summary": {"editions": 76, "first_edition": "2019-01-17",
                                   "last_edition": "2026-08-26"}}, f)
        self.meta = cms_export.export(self.out, archive=self.archive)

    def test_history_dropped_reaches_the_page(self):
        rec = self.read("states", "AL.json")["015001"]
        self.assertEqual(rec["dropped"], [["Fine", "2017-03-02", 15259, "2020-05-09"]])
        self.assertEqual(self.meta["history"],
                         {"editions": 76, "first": "2019-01-17", "last": "2026-08-26"})

    def test_a_still_published_penalty_is_not_dropped(self):
        rec = self.read("states", "AL.json")["015001"]
        self.assertEqual(rec["fines"], [["2025-01-01", 1000]])
        self.assertEqual(self.meta["dropped"], 2)

    def test_a_closed_facility_is_still_findable(self):
        index = {row[0]: row for row in self.read("index.json")}
        self.assertIn("015099", index)
        self.assertEqual(index["015099"][4], 0)      # nothing in today's window
        rec = self.read("states", "AL.json")["015099"]
        self.assertEqual(rec["dropped"], [["Payment Denial", "2018-06-01", 40, "2021-09-27"]])


if __name__ == "__main__":
    unittest.main()
