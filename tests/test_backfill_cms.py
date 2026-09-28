"""The CMS archive backfill, offline.

Remote ZIPs are built in memory and served through a fake range fetcher, so
the directory-and-member reading is exercised byte for byte without the
network. Each test pins something the real archive does: three header eras,
Fine IDs that exist only in the last, keys that are not unique, editions
re-archived unchanged, macOS junk beside the real member, and ZIPs with no
penalties file at all.
"""

import io
import os
import sys
import unittest
import zipfile

import os.path as _p
# The engine sits in tools/ beside this directory. Resolved from __file__
# rather than from ".", because these tests used to live in the
# workstation's tools/ and be run with that as the working directory -
# which made them unrunnable from the repository that owns them.
sys.path.insert(0, _p.join(_p.dirname(_p.dirname(_p.abspath(__file__))), "tools"))
import backfill_cms as bf


def zip_bytes(members, compress=zipfile.ZIP_DEFLATED):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compress) as z:
        for name, data in members.items():
            z.writestr(name, data)
    return buf.getvalue()


def fake_fetch(blobs, calls=None):
    def fetch(url, start, end=None):
        data = blobs[url]
        if start < 0:
            got = data[start:]
        else:
            got = data[start:(len(data) if end is None else end + 1)]
        if calls is not None:
            calls.append(len(got))
        return got, len(data)
    return fetch


ERA_2019 = ("provnum,provname,address,city,state,zip,pnlty_date,pnlty_type,"
            "fine_amt,payden_strt_dt,payden_days,filedate\n")
ERA_2020 = ('Federal Provider Number,Provider Name,Provider Address,Provider City,'
            'Provider State,Provider Zip Code,Penalty Date,Penalty Type,Fine Amount,'
            'Payment Denial Start Date,Payment Denial Length in Days,Location,'
            'Processing Date\n')
ERA_NOW = ('"CMS Certification Number (CCN)","Provider Name","Provider Address",'
           '"City/Town","State","ZIP Code","Penalty Date","Penalty Type","Fine ID",'
           '"Fine Amount","Payment Denial Start Date",'
           '"Payment Denial Length in Days","Location","Processing Date"\n')


def row2019(ccn, date, amount, kind="Fine"):
    return "%s,HOME %s,1 MAIN,TOWN,AL,35150,%s,%s,%s,,,2019-01-01\n" % (
        ccn, ccn, date, kind, amount)


def row2020(ccn, date, amount):
    return "%s,HOME %s,1 MAIN,TOWN,AL,35150,%s,Fine,%s,,,\"1 MAIN,TOWN\",2020-08-01\n" % (
        ccn, ccn, date, amount)


def rownow(ccn, date, amount, fine_id):
    return ('"%s","HOME %s","1 MAIN","TOWN","AL","35150",%s,Fine,%s,%s,,,'
            '"1 MAIN,TOWN",2026-08-01\n' % (ccn, ccn, date, fine_id, amount))


class TestRemoteZip(unittest.TestCase):

    def test_one_member_is_read_without_the_rest(self):
        big = os.urandom(2000000)          # incompressible, like real CSV bulk
        blob = zip_bytes({"HealthDeficiencies.csv": big,
                          "NH_Penalties_Aug2026.csv": ERA_NOW.encode()})
        calls = []
        fetch = fake_fetch({"u": blob}, calls)
        members = bf.zip_directory("u", fetch)
        m = bf.penalty_member(members)
        self.assertEqual(bf.zip_member("u", m, fetch), ERA_NOW.encode())
        self.assertLess(sum(calls), len(blob) // 2)

    def test_stored_members_are_read_too(self):
        blob = zip_bytes({"Penalties_Download.csv": b"a,b\n"}, zipfile.ZIP_STORED)
        fetch = fake_fetch({"u": blob})
        m = bf.penalty_member(bf.zip_directory("u", fetch))
        self.assertEqual(bf.zip_member("u", m, fetch), b"a,b\n")

    def test_macos_resource_forks_are_not_the_member(self):
        """2026-07-29 ships __MACOSX/._...Penalties... beside the real one."""
        members = [{"name": "__MACOSX/nh/._g6vv_NH_Penalties_Jul2026.csv"},
                   {"name": "nh/g6vv_NH_Penalties_Jul2026.csv"}]
        self.assertEqual(bf.penalty_member(members)["name"],
                         "nh/g6vv_NH_Penalties_Jul2026.csv")

    def test_a_year_end_aggregate_has_no_member(self):
        self.assertIsNone(bf.penalty_member([{"name": "NH_ProviderInfo.csv"}]))

    def test_a_short_member_is_an_error(self):
        blob = zip_bytes({"NH_Penalties.csv": b"abc" * 100})
        fetch = fake_fetch({"u": blob})
        m = bf.penalty_member(bf.zip_directory("u", fetch))
        m["usize"] += 1
        with self.assertRaises(ValueError):
            bf.zip_member("u", m, fetch)


class TestEras(unittest.TestCase):

    def test_all_three_eras_read_to_one_shape(self):
        for header, row in ((ERA_2019, row2019("015010", "2016-05-26", "15259")),
                            (ERA_2020, row2020("015010", "2016-05-26", "15259")),
                            (ERA_NOW, rownow("015010", "2016-05-26", "15259", "9"))):
            era, _, rows = bf.normalise((header + row).encode())
            self.assertEqual(bf.key_of(rows[0]),
                             ("015010", "2016-05-26", "Fine", "15259", "", ""), era)

    def test_an_unknown_header_is_refused(self):
        with self.assertRaises(ValueError):
            bf.normalise(b"ccn,date,amount\n1,2,3\n")

    def test_latin1_is_read_and_said(self):
        era, enc, rows = bf.normalise((ERA_2019 + row2019("1", "2019-01-01", "5")
                                       ).replace("TOWN", "CA\xd1ON").encode("latin-1"))
        self.assertEqual((enc, rows[0]["city"]), ("latin-1", "CA\xd1ON"))


class TestHistory(unittest.TestCase):

    def ed(self, date, text):
        return date, bf.normalise(text.encode())[2]

    def test_two_identical_fines_on_one_day_stay_two(self):
        """288 rows in Aug 2026 share a key and differ only by Fine ID."""
        h = bf.build_history([self.ed("2026-08-26", ERA_NOW
                                      + rownow("1", "2025-01-01", "650", "a")
                                      + rownow("1", "2025-01-01", "650", "b"))])
        self.assertEqual(len(h), 2)

    def test_a_penalty_followed_across_eras_is_one_penalty(self):
        h = bf.build_history([
            self.ed("2019-01-17", ERA_2019 + row2019("1", "2018-05-01", "900")),
            self.ed("2020-08-09", ERA_2020 + row2020("1", "2018-05-01", "900")),
            self.ed("2026-08-26", ERA_NOW + rownow("1", "2018-05-01", "900", "7"))])
        (rec,) = h.values()
        self.assertEqual((rec["first_seen"], rec["last_seen"], rec["appearances"],
                          rec["in_latest"], rec["fine_id"]),
                         ("2019-01-17", "2026-08-26", 3, True, "7"))

    def test_aged_out_is_kept_and_marked(self):
        h = bf.build_history([
            self.ed("2019-01-17", ERA_2019 + row2019("1", "2016-01-04", "900")),
            self.ed("2020-08-09", ERA_2020 + row2020("2", "2020-01-01", "5"))])
        old = [r for r in h.values() if r["date"] == "2016-01-04"][0]
        self.assertFalse(old["in_latest"])
        self.assertEqual(old["last_seen"], "2019-01-17")

    def test_leaving_and_coming_back_is_a_gap(self):
        h = bf.build_history([
            self.ed("a", ERA_2019 + row2019("1", "2018-01-01", "9")),
            self.ed("b", ERA_2019 + row2019("2", "2018-01-01", "9")),
            self.ed("c", ERA_2019 + row2019("1", "2018-01-01", "9"))])
        rec = [r for r in h.values() if r["ccn"] == "1"][0]
        self.assertEqual((rec["appearances"], rec["gaps"]), (2, 1))

    def test_a_revised_amount_is_one_penalty_still_published(self):
        """Fine 106114 / 2022-12-16 read $286,480, then $186,212. Unlinked,
        the page would list the old figure as dropped while CMS publishes
        the new one."""
        h = bf.build_history([
            self.ed("a", ERA_2019 + row2019("1", "2018-01-01", "286480")),
            self.ed("b", ERA_2019 + row2019("1", "2018-01-01", "186212"))])
        pens, linked, ambiguous = bf.link_restatements(h)
        self.assertEqual((len(pens), linked, ambiguous), (1, 1, 0))
        (p,) = pens
        self.assertEqual((p["amount"], p["first_amount"], p["revisions"],
                          p["first_seen"], p["in_latest"]),
                         ("186212", "286480", 1, "a", True))

    def test_two_versions_starting_together_are_not_guessed_at(self):
        h = bf.build_history([
            self.ed("a", ERA_2019 + row2019("1", "2018-01-01", "900")),
            self.ed("b", ERA_2019 + row2019("1", "2018-01-01", "450")
                    + row2019("1", "2018-01-01", "300"))])
        pens, linked, ambiguous = bf.link_restatements(h)
        self.assertEqual((len(pens), linked), (3, 0))
        self.assertEqual(ambiguous, 2)

    def test_a_penalty_that_simply_ended_is_not_linked_to_a_later_one(self):
        h = bf.build_history([
            self.ed("a", ERA_2019 + row2019("1", "2018-01-01", "900")),
            self.ed("b", ERA_2019 + row2019("2", "2019-01-01", "5")),
            self.ed("c", ERA_2019 + row2019("1", "2018-01-01", "450"))])
        pens, linked, _ = bf.link_restatements(h)
        self.assertEqual(linked, 0)
        self.assertEqual(sum(p["ccn"] == "1" for p in pens), 2)


class TestBuild(unittest.TestCase):

    def entry(self, date):
        return {"date": date, "url": "/x/%s.zip" % date, "name": "Theme (%s)" % date}

    def test_a_re_archived_edition_is_counted_once(self):
        raw = (ERA_2019 + row2019("1", "2018-01-01", "9")).encode()
        manifest, editions, failed = bf.build([
            (self.entry("2020-11-21"), "NH_Penalties_Nov2020.csv", raw, "fetched"),
            (self.entry("2020-12-21"), "NH_Penalties_Nov2020.csv", raw, "fetched")])
        self.assertEqual(len(editions), 1)
        self.assertEqual(manifest[1]["duplicate_of"], "2020-11-21")

    def test_a_capture_the_archive_already_holds_is_counted_once_and_keeps_cms_link(self):
        import gzip
        import shutil
        import tempfile
        raw = (ERA_NOW + rownow("1", "2025-01-01", "900", "7")).encode()
        root = tempfile.mkdtemp()
        try:
            os.makedirs(os.path.join(root, "2026-09-18"))
            with gzip.open(os.path.join(root, "2026-09-18", "penalties.csv.gz"), "wb") as fh:
                fh.write(raw)
            caps = bf.captured(root)
        finally:
            shutil.rmtree(root)
        self.assertEqual(caps[0][0]["date"], "2026-09-18")
        self.assertTrue(caps[0][0]["link"].endswith("snapshots/cms/2026-09-18/penalties.csv.gz"))
        manifest, editions, _ = bf.build(
            [(self.entry("2026-08-26"), "NH_Penalties_Aug2026.csv", raw, "cache")] + caps)
        self.assertEqual(len(editions), 1)
        self.assertEqual(manifest[0]["archive"], "https://data.cms.gov/x/2026-08-26.zip")
        self.assertEqual(manifest[1]["duplicate_of"], "2026-08-26")

    def test_a_new_capture_is_the_latest_edition(self):
        old = (ERA_NOW + rownow("1", "2023-01-01", "900", "7")).encode()
        new = (ERA_NOW + rownow("1", "2026-09-01", "50", "8")).encode()
        cap = ({"date": "2026-10-02", "id": "capture", "name": "c", "link": "gh"},
               "penalties.csv.gz", new, "capture")
        manifest, editions, _ = bf.build(
            [(self.entry("2026-08-26"), "NH_Penalties_Aug2026.csv", old, "cache"), cap])
        pens, _, _ = bf.link_restatements(bf.build_history(editions))
        latest = {p["date"]: p["in_latest"] for p in pens}
        self.assertEqual(latest, {"2023-01-01": False, "2026-09-01": True})
        self.assertEqual(manifest[1]["archive"], "gh")

    def test_a_failed_archive_is_named(self):
        _, _, failed = bf.build([(self.entry("2021-01-27"), None, None,
                                  "FAILED: IOError: reset")])
        self.assertEqual(len(failed), 1)


if __name__ == "__main__":
    unittest.main(verbosity=1)
