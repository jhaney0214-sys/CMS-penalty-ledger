"""findings.py sorts each dropped penalty into exactly one bucket.

Built from rows shaped like penalties_history.csv.gz, one per case the
article distinguishes, so a change to any rule moves one number here.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "tools"))
import findings                                               # noqa: E402


def row(ccn, date, last_seen, in_latest="no", amount="1000", kind="Fine",
        first_seen=None):
    return {"ccn": ccn, "kind": kind, "date": date, "amount": amount,
            "denial_days": "", "first_seen": first_seen or date,
            "last_seen": last_seen, "in_latest": in_latest}


class Buckets(unittest.TestCase):

    def setUp(self):
        self.rows = [
            # aged out: last listed three years after its date
            row("A", "2019-01-10", "2022-01-27"),
            # still published
            row("B", "2025-01-10", "2026-08-26", in_latest="yes"),
            # early, facility no longer inspected
            row("C", "2024-01-10", "2024-06-27", amount="5000"),
            # early, and the same fine re-appears under a new date
            row("D", "2024-01-10", "2024-06-27", amount="7000"),
            row("D", "2024-02-10", "2026-08-26", in_latest="yes",
                amount="7000", first_seen="2024-07-25"),
            # early, unexplained
            row("E", "2024-01-10", "2024-06-27", amount="250000"),
        ]
        self.surveyed = {"A", "B", "D", "E", "F"}
        self.out = findings.figures(self.rows, self.surveyed)

    def test_every_dropped_penalty_lands_in_one_bucket(self):
        o = self.out
        self.assertEqual(o["dropped"], 4)
        self.assertEqual(o["aged_out"], 1)
        self.assertEqual(o["early"], 3)
        self.assertEqual(o["early_facility_gone"] + o["early_redated"]
                         + o["early_unexplained"], o["early"])

    def test_a_redated_fine_is_not_a_removal(self):
        self.assertEqual(self.out["early_redated"], 1)
        self.assertEqual(self.out["early_unexplained"], 1)
        self.assertEqual(self.out["early_unexplained_fines"],
                         {"count": 1, "dollars": 250000})

    def test_no_current_penalty_counts_only_inspected_homes(self):
        o = self.out
        # B and D have a current row; A, E, F do not. F never had one.
        self.assertEqual(o["surveyed_with_current_penalty"], 2)
        self.assertEqual(o["surveyed_without_current_penalty"], 3)
        self.assertEqual(o["without_current_but_penalised_before"], 2)
        self.assertEqual(o["without_current_dropped_fines_at_least"]["100000"], 1)

    def test_a_home_no_longer_inspected_is_never_counted_as_clean(self):
        self.assertNotIn("C", self.surveyed)
        self.assertEqual(self.out["surveyed"], 5)


if __name__ == "__main__":
    unittest.main()
