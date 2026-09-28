"""Facility pages: what a lawyer reads, so every row must say what CMS published.

Built on rows shaped exactly like penalties_history.csv.gz, using Siesta Key's
(105407) real history as the pattern: a fine revised from $125,970 to
$799,880, three names, and penalties from before CMS's current window.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
import facility_pages  # noqa: E402

URLS = {d: "https://data.cms.gov/archive/nursing-homes_%s.zip" % d
        for d in ("2019-01-17", "2019-12-17", "2019-12-31", "2023-05-30", "2025-12-15",
                  "2025-12-31", "2026-08-26")}
TODAY = {"name": "SIESTA KEY HEALTH AND REHABILITATION CENTER", "city": "SARASOTA", "state": "FL"}


def row(**kw):
    base = {"ccn": "105407", "name": TODAY["name"], "date": "2022-12-08", "kind": "Fine",
            "amount": "799880", "denial_start": "", "denial_days": "", "first_amount": "",
            "revisions": "0", "first_seen": "2023-05-30", "last_seen": "2025-12-15",
            "appearances": "27", "gaps": "0", "in_latest": "no"}
    base.update(kw)
    return base


class Pages(unittest.TestCase):

    def page(self, rows):
        return facility_pages.facility_page("105407", rows, URLS, TODAY, "2026-09-27")

    def test_a_revised_fine_shows_both_amounts(self):
        text = self.page([row(first_amount="125970", revisions="2")])
        self.assertIn("$799,880", text)
        self.assertIn("first published as $125,970; revised 2 times", text)
        self.assertIn("1 fine changed amount", text)

    def test_every_listing_date_links_to_its_cms_edition(self):
        text = self.page([row()])
        self.assertIn('<a href="%s">2023-05-30</a>' % URLS["2023-05-30"], text)
        self.assertIn('<a href="%s">2025-12-15</a>' % URLS["2025-12-15"], text)
        self.assertIn('Not shown since <a href="%s">2025-12-31</a>' % URLS["2025-12-31"], text)

    def test_an_earlier_name_is_named_on_its_row_and_at_the_top(self):
        old = row(name="SPRINGWOOD CENTER", date="2016-12-09", amount="4071",
                  first_seen="2019-01-17", last_seen="2019-12-17", appearances="11")
        text = self.page([row(), old])
        self.assertIn("Earlier names in CMS's files:</strong> Springwood Center.", text)
        self.assertIn("as Springwood Center", text)
        self.assertIn("back to 2016", text)

    def test_an_early_removal_is_flagged_and_an_aged_out_one_is_not(self):
        early = row(date="2024-01-01", last_seen="2025-12-15")
        aged = row(date="2016-12-09", first_seen="2019-01-17", last_seen="2019-12-17")
        self.assertIn("before the usual three; the file does not say why", self.page([early]))
        self.assertNotIn("before the usual three", self.page([aged]))

    def test_the_summary_counts_shown_and_dropped(self):
        text = self.page([row(), row(in_latest="yes", amount="1000", date="2025-01-01")])
        self.assertIn("CMS's current file shows 1 penalty for this home", text)
        self.assertIn("1 fine totalling $799,880 that the current file no longer shows", text)

    def test_a_payment_denial_is_in_days_not_dollars(self):
        text = self.page([row(kind="Payment Denial", amount="", denial_days="92",
                              denial_start="2023-02-18")])
        self.assertIn("payment denial, 92 days from 2023-02-18", text)

    def test_a_name_with_markup_is_escaped(self):
        today = dict(TODAY, name="A & B <CARE>")
        text = facility_pages.facility_page("105407", [row(name="A & B <CARE>")], URLS, today, "2026-09-27")
        self.assertIn("A &amp; B &lt;care&gt;", text)
        self.assertNotIn("<CARE>", text)


class Ratings(unittest.TestCase):

    RUNS = [
        {"from_edition": "2019-01-17", "to_edition": "2021-06-27", "overall": "4", "inspection": "4",
         "quality": "5", "staffing": "3", "in_latest": "no"},
        {"from_edition": "2021-07-28", "to_edition": "2026-08-26", "overall": "1", "inspection": "1",
         "quality": "", "staffing": "3", "in_latest": "yes"}]

    def page(self, runs, moved):
        return facility_pages.facility_page("105407", [row()], URLS, TODAY, "2026-09-27", runs, moved)

    def test_newest_first_with_a_summary(self):
        text = self.page(self.RUNS, {})
        self.assertIn("Overall rating in CMS's files: 4 in 2019-01, 1 now. Lowest 1, highest 4, "
                      "across 1 change.", text)
        self.assertLess(text.index("2021-07-28 to now"), text.index("2019-01-17 to 2021-06-27"))

    def test_only_an_exceptional_edition_is_marked_on_its_row(self):
        self.assertIn("53% of rated homes changed overall rating in this edition",
                      self.page(self.RUNS, {"2021-07-28": 0.53}))
        self.assertNotIn("in this edition", self.page(self.RUNS, {"2021-07-28": 0.24}))

    def test_the_quarterly_pattern_is_stated_once(self):
        moved = {"2021-02": 0.04, "2021-03": 0.05, "2021-04": 0.25, "2021-05": 0.03, "2021-07-28": 0.30}
        self.assertIn("In most editions about 4% of rated homes change overall rating; in CMS's "
                      "quarterly refreshes 25&ndash;30% do", self.page(self.RUNS, moved))

    def test_a_component_change_within_a_stretch_is_a_range_not_a_new_row(self):
        runs = [dict(self.RUNS[0], to_edition="2019-12-17"),
                dict(self.RUNS[0], from_edition="2020-01-09", staffing="2"),
                self.RUNS[1]]
        moved = {"2019-12-17": 0.05, "2020-01-09": 0.27, "2021-07-28": 0.1}
        text = self.page(runs, moved)
        self.assertIn("2019-01-17 to 2021-06-27", text)
        self.assertIn("2&ndash;3&#9733;", text)
        self.assertIn("across 1 change.", text)

    def test_a_missing_star_says_not_rated(self):
        self.assertIn("not rated", self.page(self.RUNS, {}))

    def test_no_ratings_means_no_section(self):
        self.assertNotIn("Star ratings over time", self.page(None, None))


class Choosing(unittest.TestCase):

    def test_ranked_by_dropped_fines_among_homes_inspected_today(self):
        rows = [row(ccn="1", amount="500"), row(ccn="2", amount="900"), row(ccn="3", amount="5000"),
                row(ccn="2", amount="900", in_latest="yes"), row(ccn="1", kind="Payment Denial", amount="")]
        self.assertEqual(facility_pages.choose(rows, {"1", "2"}, 5), ["2", "1"])

    def test_the_next_edition_is_the_one_a_penalty_was_gone_from(self):
        dates = sorted(URLS)
        self.assertEqual(facility_pages.next_edition(dates, "2025-12-15"), "2025-12-31")
        self.assertIsNone(facility_pages.next_edition(dates, "2026-08-26"))

    def test_an_edition_is_never_a_duplicate_or_a_zip_without_penalties(self):
        import json
        import tempfile
        archives = [
            {"archive_date": "2020-11-27", "archive": "u1", "member": "p.csv", "sha256": "a"},
            {"archive_date": "2020-12-21", "archive": "u2", "member": "p.csv", "duplicate_of": "2020-11-27"},
            {"archive_date": "2020-12-31", "archive": "u3", "member": None},
            {"archive_date": "2021-01-27", "archive": "u4", "member": "p.csv", "sha256": "b"}]
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
            json.dump({"archives": archives}, fh)
        try:
            urls = facility_pages.editions(fh.name)
        finally:
            os.remove(fh.name)
        self.assertEqual(urls, {"2020-11-27": "u1", "2021-01-27": "u4"})
        self.assertEqual(facility_pages.next_edition(list(urls), "2020-11-27"), "2021-01-27")

    def test_a_name_reads_as_a_title(self):
        self.assertEqual(facility_pages.display("MORGANTOWN HEIGHTS OF JOURNEY"), "Morgantown Heights of Journey")
        self.assertEqual(facility_pages.display("THE VILLAGES AT LAPEER LLC"), "The Villages at Lapeer LLC")

    def test_all_means_every_home_with_a_dropped_fine_and_no_other(self):
        rows = [row(ccn="1", amount="500"), row(ccn="2", amount="900", in_latest="yes"),
                row(ccn="3", kind="Payment Denial", amount=""), row(ccn="4", amount="70")]
        self.assertEqual(facility_pages.choose(rows, {"1", "2", "3", "4"}, None), ["1", "4"])

    def test_a_page_carries_its_edition_not_the_build_day(self):
        text = facility_pages.facility_page("105407", [row()], URLS, TODAY, "2030-01-01")
        self.assertIn("through the 2026-08-26 edition", text)
        self.assertNotIn("2030-01-01", text)

    def test_a_page_links_its_state_and_names_its_clean_url(self):
        text = facility_pages.facility_page("105407", [row()], URLS, TODAY, "2026-09-27")
        self.assertIn('<a href="state-fl.html">FL</a>', text)
        self.assertIn('<link rel="canonical" href="https://penalty-ledger.pages.dev/facilities/'
                      '105407-siesta-key-health-and-rehabilitation-center">', text)

    def test_the_sitemap_lists_clean_urls_dated_to_the_edition(self):
        text = facility_pages.sitemap(["", "state-fl.html", "105407-siesta-key.html"], "2026-08-26")
        self.assertIn("<loc>https://penalty-ledger.pages.dev/</loc>", text)
        self.assertIn("<loc>https://penalty-ledger.pages.dev/facilities/105407-siesta-key</loc>", text)
        self.assertIn("<loc>https://penalty-ledger.pages.dev/facilities/state-fl</loc>", text)
        self.assertEqual(text.count("<lastmod>2026-08-26</lastmod>"), 4)
        self.assertNotIn(".html", text)

    def test_a_state_index_ranks_largest_first(self):
        entries = [{"page": "a.html", "name": "A", "city": "X", "state": "FL", "dollars": 10.0, "dropped": 1},
                   {"page": "b.html", "name": "B", "city": "X", "state": "FL", "dollars": 99.0, "dropped": 2}]
        text = facility_pages.state_index("FL", entries, "2026-08-26")
        self.assertLess(text.index('href="b.html"'), text.index('href="a.html"'))
        self.assertIn("largest first: 2.", text)

    def test_a_page_name_is_stable_and_safe(self):
        self.assertEqual(facility_pages.page_name("105407", "Siesta Key Health & Rehab, LLC"),
                         "105407-siesta-key-health-rehab-llc.html")


if __name__ == "__main__":
    unittest.main()
