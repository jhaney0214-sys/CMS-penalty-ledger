"""The CMS ledger, driven against fixture captures built in a temp directory.

Most of these are about what the tool must refuse to say. The archive's own
README and `snapshot_cms.py` both argue that a silent gap is worse than a
stated one, and a reporting tool is where that argument either holds or
quietly stops holding:

* a facility with no penalty row must never read as a clean facility, and a
  CCN that is not in the capture at all must not read as either - "could not
  look" and "looked and found nothing" have now been recorded five times in
  this workstation as the same output;
* fines and payment denials must never be added together, because they are
  money and days;
* one capture must not report as "no change", which is what an empty diff
  looks like;
* a dropped row must be reported as dropped, since that is the single fact
  the archive exists to preserve and no later fetch can recover it.

The last file in here is the only test that reads the real archive: it pins
the figures the archive's README publishes to the bytes it publishes them
about, and skips rather than fails when no capture is present.
"""

import gzip
import io
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
import cms_ledger                 # with PYTHONPATH=. - see tools/README.md


PENALTY_HEADER = ('"CMS Certification Number (CCN)","Provider Name",'
                  '"Provider Address","City/Town","State","ZIP Code",'
                  '"Penalty Date","Penalty Type","Fine ID","Fine Amount",'
                  '"Payment Denial Start Date",'
                  '"Payment Denial Length in Days","Location",'
                  '"Processing Date"')

SURVEY_COLUMNS = [
    "CMS Certification Number (CCN)", "Provider Name", "Provider Address",
    "City/Town", "State", "ZIP Code", "Inspection Cycle",
    "Health Survey Date", "Fire Safety Survey Date",
    "Total Number of Health Deficiencies",
    "Total Number of Fire Safety Deficiencies",
    "Count of Freedom from Abuse and Neglect and Exploitation Deficiencies",
    "Count of Quality of Life and Care Deficiencies",
    "Count of Infection Control Deficiencies", "Location", "Processing Date",
]


def fine(ccn="015019", name="MERRY WOOD LODGE", state="AL",
         date="2024-09-01", fine_id="38063", amount="182968"):
    return ",".join(['"%s"' % ccn, '"%s"' % name, '"280 MT HEBRON ROAD"',
                     '"ELMORE"', '"%s"' % state, '"36025"', date, "Fine",
                     fine_id, amount, "", "", '"ELMORE,%s"' % state,
                     "2026-08-01"])


def denial(ccn="015019", name="MERRY WOOD LODGE", state="AL",
           date="2024-09-01", start="2024-10-01", days="42"):
    return ",".join(['"%s"' % ccn, '"%s"' % name, '"280 MT HEBRON ROAD"',
                     '"ELMORE"', '"%s"' % state, '"36025"', date,
                     "Payment Denial", "", "", start, days,
                     '"ELMORE,%s"' % state, "2026-08-01"])


def survey(ccn="015019", name="MERRY WOOD LODGE", state="AL", cycle="1",
           health="8", fire="6", abuse="3"):
    values = [ccn, name, "280 MT HEBRON ROAD", "ELMORE", state, "36025",
              cycle, "2024-09-01", "2024-09-01", health, fire, abuse, "1",
              "0", "ELMORE", "2026-08-01"]
    return ",".join('"%s"' % v for v in values)


class Archive(object):
    """A temp directory shaped exactly like snapshots/cms/."""

    def __init__(self):
        self.root = tempfile.mkdtemp()

    def capture(self, stamp, penalties, surveys=None, not_kept=True):
        path = os.path.join(self.root, stamp)
        os.makedirs(path)
        self._write(os.path.join(path, "penalties.csv.gz"),
                    [PENALTY_HEADER] + list(penalties))
        self._write(os.path.join(path, "survey_summary.csv.gz"),
                    [",".join('"%s"' % c for c in SURVEY_COLUMNS)]
                    + list(surveys or [survey()]))
        manifest = {
            "captured": stamp,
            "files": [{"name": "penalties", "title": "Penalties",
                       "archived": True}],
        }
        if not_kept:
            manifest["files"].append({"name": "health_deficiencies",
                                      "title": "Health Deficiencies",
                                      "archived": False})
        import json
        with open(os.path.join(path, "manifest.json"), "w",
                  encoding="utf-8") as handle:
            json.dump(manifest, handle)
        return path

    @staticmethod
    def _write(path, lines):
        with gzip.open(path, "wt", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")

    def close(self):
        shutil.rmtree(self.root, ignore_errors=True)


class LedgerTest(unittest.TestCase):

    def setUp(self):
        self.archive = Archive()
        self.addCleanup(self.archive.close)

    def one(self, penalties, surveys=None, stamp="2026-09-18"):
        self.archive.capture(stamp, penalties, surveys)
        return cms_ledger.load(archive=self.archive.root)


class RefusesToSayMoreThanItKnows(LedgerTest):

    def test_a_facility_with_no_penalty_is_not_reported_clean(self):
        capture = self.one([fine(ccn="015060", name="SOMEWHERE ELSE")],
                           surveys=[survey(), survey(ccn="015060",
                                                     name="SOMEWHERE ELSE")])
        record = cms_ledger.facility(capture, "015019")
        text = cms_ledger.facility_report(capture, record)
        self.assertIn(cms_ledger.NOT_CLEAN, text)
        self.assertNotIn("0 fine", text)

    def test_a_ccn_not_in_the_capture_is_not_a_facility_with_no_penalties(self):
        capture = self.one([fine()])
        self.assertIsNone(cms_ledger.facility(capture, "999999"))

    def test_the_state_report_carries_its_denominator(self):
        # One facility fined, two more surveyed and never penalised. A report
        # that printed only the fined one would read as a 100% penalty rate.
        capture = self.one(
            [fine()],
            surveys=[survey(), survey(ccn="015060", name="B"),
                     survey(ccn="015061", name="C")])
        summary = cms_ledger.state_summary(capture, "AL")
        self.assertEqual(summary["surveyed"], 3)
        self.assertEqual(summary["penalised"], 1)
        self.assertEqual(summary["clean"], 2)
        text = cms_ledger.state_report(capture, "AL")
        self.assertIn("3 facilities surveyed", text)
        self.assertIn(cms_ledger.NOT_CLEAN, text)

    def test_an_unknown_state_says_so_rather_than_reporting_zeros(self):
        capture = self.one([fine()])
        text = cms_ledger.state_report(capture, "ZZ")
        self.assertIn("No facility in ZZ", text)
        self.assertNotIn("0 fines", text)

    def test_every_report_names_the_files_the_archive_does_not_hold(self):
        capture = self.one([fine()])
        for text in (cms_ledger.state_report(capture, "AL"),
                     cms_ledger.facility_report(
                         capture, cms_ledger.facility(capture, "015019"))):
            self.assertIn("Health Deficiencies", text)
            self.assertIn("manifest only", text)

    def test_the_window_is_read_off_the_rows_not_hardcoded(self):
        capture = self.one([fine(date="2021-01-04", fine_id="1"),
                            fine(date="2021-06-30", fine_id="2")])
        self.assertEqual(capture.window, ("2021-01-04", "2021-06-30"))
        text = cms_ledger.state_report(capture, "AL")
        self.assertIn("2021-01-04 to 2021-06-30", text)
        self.assertIn("rolling window opening", text)


class MoneyAndDaysStaySeparate(LedgerTest):

    def test_a_denial_never_lands_in_the_money_total(self):
        capture = self.one([fine(amount="1000"), denial(days="42")])
        summary = cms_ledger.state_summary(capture, "AL")
        self.assertEqual(summary["fine_total"], 1000)
        self.assertEqual(len(summary["denials"]), 1)
        text = cms_ledger.facility_report(
            capture, cms_ledger.facility(capture, "015019"))
        self.assertIn("$1,000", text)
        self.assertIn("42 days", text)
        self.assertNotIn("$1,042", text)

    def test_both_kinds_are_reported_even_when_one_is_absent(self):
        capture = self.one([denial(days="42")])
        text = cms_ledger.state_report(capture, "AL")
        self.assertIn("No fine recorded", text)
        self.assertIn("1 payment denials", text)


class ChangesBetweenCaptures(LedgerTest):

    def two(self, first, second):
        self.archive.capture("2026-09-18", first)
        self.archive.capture("2026-10-18", second)
        return cms_ledger.captures(self.archive.root)

    def test_one_capture_is_not_reported_as_no_change(self):
        self.archive.capture("2026-09-18", [fine()])
        text = cms_ledger.changes_report(cms_ledger.captures(
            self.archive.root))
        self.assertIn("nothing to compare", text)
        self.assertIn("This is not 'no change'", text)

    def test_a_row_that_aged_out_is_reported_as_dropped(self):
        held = self.two([fine(fine_id="1"), fine(fine_id="2", date="2023-09-01")],
                        [fine(fine_id="1")])
        diff = cms_ledger.changes(held[0], held[1])
        self.assertEqual([p["fine_id"] for p in diff["dropped"]], ["2"])
        self.assertEqual(diff["added"], [])
        text = cms_ledger.changes_report(held)
        self.assertIn("dropped:     1 rows", text)
        self.assertIn("no later fetch can bring it back", text)

    def test_a_new_row_is_reported_as_added(self):
        held = self.two([fine(fine_id="1")],
                        [fine(fine_id="1"), fine(fine_id="9", amount="500")])
        diff = cms_ledger.changes(held[0], held[1])
        self.assertEqual([p["fine_id"] for p in diff["added"]], ["9"])
        self.assertEqual(diff["dropped"], [])

    def test_a_renamed_provider_is_not_a_dropped_fine(self):
        # CMS restates provider names between editions; a fine is the same
        # fine. Matching on the row rather than the id would report this as
        # a penalty disappearing, which is the one thing this tool must not
        # get wrong.
        held = self.two([fine(fine_id="1", name="OLD NAME")],
                        [fine(fine_id="1", name="NEW NAME LLC")])
        diff = cms_ledger.changes(held[0], held[1])
        self.assertEqual(diff["dropped"], [])
        self.assertEqual(diff["added"], [])

    def test_a_restated_denial_reads_as_a_drop_and_an_add(self):
        # Documented rather than fixed: denials carry no id, so this is what
        # the composite key costs. The report says so; this pins that it
        # stays true of the data as well as of the prose.
        held = self.two([denial(days="42")], [denial(days="45")])
        diff = cms_ledger.changes(held[0], held[1])
        self.assertEqual(len(diff["dropped"]), 1)
        self.assertEqual(len(diff["added"]), 1)
        self.assertIn("restated denial", cms_ledger.changes_report(held))

    def test_two_denials_on_one_day_are_two_rows(self):
        capture = self.one([denial(start="2024-10-01", days="42"),
                            denial(start="2024-12-01", days="10")])
        keys = {cms_ledger.penalty_key(p) for p in capture.penalties}
        self.assertEqual(len(keys), 2)


class CommandLine(LedgerTest):

    def run_main(self, *args):
        self.archive.capture("2026-09-18", [fine(), denial()])
        out = io.StringIO()
        stdout, sys.stdout = sys.stdout, out
        archive, cms_ledger.ARCHIVE = cms_ledger.ARCHIVE, self.archive.root
        try:
            code = cms_ledger.main(["cms_ledger.py"] + list(args))
        finally:
            sys.stdout = stdout
            cms_ledger.ARCHIVE = archive
        return code, out.getvalue()

    def test_a_ccn_missing_its_leading_zero_still_finds_the_facility(self):
        code, text = self.run_main("--ccn", "15019")
        self.assertEqual(code, 0)
        self.assertIn("CCN 015019", text)

    def test_an_unknown_ccn_exits_non_zero_and_says_it_found_nothing(self):
        code, text = self.run_main("--ccn", "999999")
        self.assertEqual(code, 1)
        self.assertIn("No facility with CCN", text)
        self.assertNotIn(cms_ledger.NOT_CLEAN, text)

    def test_search_by_name_is_case_insensitive(self):
        code, text = self.run_main("--name", "merry wood")
        self.assertEqual(code, 0)
        self.assertIn("MERRY WOOD LODGE", text)

    def test_no_argument_prints_help_rather_than_a_default_report(self):
        code, text = self.run_main()
        self.assertEqual(code, 2)
        self.assertIn("--captures", text)


REAL = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(
    os.path.dirname(__file__)))), "snapshots", "cms")


@unittest.skipUnless(os.path.isdir(REAL) and cms_ledger.captures(REAL),
                     "no real capture held")
class TheArchivesPublishedFigures(unittest.TestCase):
    """Pins snapshots/cms/README.md's numbers to the bytes it describes.

    The README states them in prose, which is exactly the kind of claim this
    workstation has repeatedly found to be true when written and false a
    month later.
    """

    @classmethod
    def setUpClass(cls):
        cls.capture = cms_ledger.load("2026-09-18", archive=REAL)

    def test_the_first_capture_holds_what_the_readme_says(self):
        fines, denials = cms_ledger.split(self.capture.penalties)
        self.assertEqual(len(self.capture.penalties), 15696)
        self.assertEqual(len({p["ccn"] for p in self.capture.penalties}), 6775)
        self.assertEqual(len(fines), 13256)
        self.assertEqual(len(denials), 2440)
        self.assertEqual(sum(f["amount"] for f in fines), 456752787)
        self.assertEqual(self.capture.window, ("2023-08-19", "2026-07-29"))
        self.assertEqual(self.capture.processing_date, "2026-08-01")

    def test_every_penalised_facility_is_also_in_the_survey_file(self):
        # The state report's denominator comes from the survey file, so a
        # penalised CCN missing from it would be counted against nothing.
        penalised = {p["ccn"] for p in self.capture.penalties}
        self.assertEqual(penalised - self.capture.facilities(), set())

    def test_no_penalty_row_is_both_a_fine_and_a_denial(self):
        for pen in self.capture.penalties:
            if pen["kind"] == cms_ledger.FINE:
                self.assertIsNone(pen["denial_days"])
            else:
                self.assertIsNone(pen["amount"])
                self.assertEqual(pen["fine_id"], "")

    def test_the_penalty_key_is_unique_across_the_whole_capture(self):
        keys = [cms_ledger.penalty_key(p) for p in self.capture.penalties]
        self.assertEqual(len(set(keys)), len(keys))


class TestHistory(unittest.TestCase):
    """The rebuilt history from `backfill_cms.py`: every penalty CMS has
    published since 2019-01, including the ones it has since dropped."""

    HEADER = ("ccn,name,address,city,state,zip,date,kind,fine_id,amount,"
              "denial_start,denial_days,first_seen,last_seen,appearances,"
              "gaps,in_latest\n")

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.path = os.path.join(self.tmp, "h.csv.gz")
        rows = [
            "015019,MERRY WOOD,1 A,ELMORE,AL,1,2016-05-26,Fine,,15259,,,"
            "2019-01-17,2019-06-09,6,0,no",
            "015019,MERRY WOOD,1 A,ELMORE,AL,1,2024-09-01,Fine,38063,182968,,,"
            "2024-10-30,2026-08-26,20,1,yes",
            "015019,MERRY WOOD,1 A,ELMORE,AL,1,2024-09-01,Payment Denial,,,"
            "2024-10-01,42,2024-10-30,2026-08-26,20,0,yes",
            "999999,OTHER,1 B,X,TX,1,2020-01-01,Fine,,100,,,2020-08-09,2023-01-02,5,0,no",
        ]
        with gzip.open(self.path, "wt", encoding="utf-8") as fh:
            fh.write(self.HEADER + "\n".join(rows) + "\n")

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_no_history_built_is_not_no_penalties(self):
        text = cms_ledger.history_report("015019", cms_ledger.history_for(
            "015019", os.path.join(self.tmp, "missing.csv.gz")))
        self.assertIn("backfill_cms.py", text)
        self.assertNotIn(cms_ledger.NOT_CLEAN, text)

    def test_a_dropped_penalty_is_shown_with_when_cms_published_it(self):
        text = cms_ledger.history_report("15019", cms_ledger.history_for("15019", self.path))
        self.assertIn("published 2019-01-17 .. 2019-06-09", text)
        self.assertIn("1 of these is no longer in CMS's current file", text)
        self.assertIn("[left and came back]", text)

    def test_fines_and_denials_stay_apart_in_history(self):
        text = cms_ledger.history_report("015019", cms_ledger.history_for("015019", self.path))
        self.assertIn("2 fine(s) totalling $198,227; 1 payment denial(s)", text)

    def test_no_history_row_is_not_a_clean_record(self):
        text = cms_ledger.history_report("123456", cms_ledger.history_for("123456", self.path))
        self.assertIn(cms_ledger.NOT_CLEAN, text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
