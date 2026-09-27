#!/usr/bin/env python3
"""The figures a written piece about this ledger quotes, computed, not typed.

    python tools/findings.py            # from the repository root
    python tools/findings.py --json

Every number in the article comes from here, so the article can be re-checked
against the archive by running one command instead of trusting the prose.

## Two ways a penalty leaves CMS's current file

**It ages out.** CMS publishes a rolling three-year window, so a penalty's last
appearance is about three years after its date. The distribution of
(last edition listing it - penalty date) has a cliff: a few hundred a year
below 2.85 years, tens of thousands just above it. Everything at or past the
cliff is counted as aged out.

**It goes early.** A penalty last listed less than 2.85 years after its date
left inside the window. The data does not say why. Two causes are separable
and separated here:

  - the facility is no longer in CMS's inspection file (closed, or re-certified
    under a new number), so all its rows went together;
  - the same penalty re-appears later under a new date: same facility, kind
    and amount, first listed on or after the old row's last listing. That is a
    correction of the date, not a removal.

What is left after both is reported as unexplained, and the article says
exactly that rather than guessing at appeals or settlements.

## "No penalty" homes

A facility CMS inspects today with no row in its current penalty file reads as
penalty-free on every consumer tool built on that file. The count that matters
is how many of those had penalties in an earlier edition.
"""

import argparse
import collections
import csv
import datetime
import gzip
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import cms_ledger                                             # noqa: E402

CLIFF_YEARS = 2.85
THRESHOLDS = (100000, 1000000)


def _years(row):
    last = datetime.date.fromisoformat(row["last_seen"])
    dated = datetime.date.fromisoformat(row["date"])
    return (last - dated).days / 365.25


def _amount(row):
    return float(row["amount"]) if row["amount"] else 0.0


def _size(row):
    return row["amount"] or row["denial_days"]


def read_history(path=cms_ledger.HISTORY):
    with gzip.open(path, "rt", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def figures(rows, surveyed):
    """Every quoted figure, from the history rows and the set of CCNs CMS
    inspects today."""
    current = [r for r in rows if r["in_latest"] == "yes"]
    dropped = [r for r in rows if r["in_latest"] == "no"]
    early = [r for r in dropped if _years(r) < CLIFF_YEARS]

    later = collections.defaultdict(list)
    for r in rows:
        later[(r["ccn"], r["kind"], _size(r))].append(r)

    def redated(row):
        return any(other is not row and other["first_seen"] >= row["last_seen"]
                   for other in later[(row["ccn"], row["kind"], _size(row))])

    early_closed = [r for r in early if r["ccn"] not in surveyed]
    early_open = [r for r in early if r["ccn"] in surveyed]
    early_redated = [r for r in early_open if redated(r)]
    unexplained = [r for r in early_open if not redated(r)]

    with_current = {r["ccn"] for r in current} & surveyed
    no_current = surveyed - with_current
    had_dropped = {r["ccn"] for r in dropped} & no_current

    dropped_fines = collections.defaultdict(float)
    dropped_rows = collections.defaultdict(list)
    for r in dropped:
        if r["ccn"] in had_dropped and r["kind"] == "Fine":
            dropped_fines[r["ccn"]] += _amount(r)
            dropped_rows[r["ccn"]].append(r["date"])

    # The article's table of homes, so it is printed here rather than typed:
    # added 2026-09-26, when a desktop check found the table matched the data
    # to the dollar but no command produced it.
    top = sorted((c for c, v in dropped_fines.items() if v >= THRESHOLDS[-1]),
                 key=lambda c: -dropped_fines[c])

    def fines(group):
        chosen = [r for r in group if r["kind"] == "Fine"]
        return {"count": len(chosen),
                "dollars": round(sum(_amount(r) for r in chosen))}

    return {
        "penalties": len(rows),
        "editions": len({r["first_seen"] for r in rows} | {r["last_seen"] for r in rows}),
        "current": len(current),
        "dropped": len(dropped),
        "dropped_fines": fines(dropped),
        "earliest_dropped": min((r["date"] for r in dropped), default=None),
        "aged_out": len(dropped) - len(early),
        "early": len(early),
        "early_facility_gone": len(early_closed),
        "early_redated": len(early_redated),
        "early_unexplained": len(unexplained),
        "early_unexplained_fines": fines(unexplained),
        "surveyed": len(surveyed),
        "surveyed_with_current_penalty": len(with_current),
        "surveyed_without_current_penalty": len(no_current),
        "without_current_but_penalised_before": len(had_dropped),
        "without_current_dropped_fines_at_least": {
            str(t): sum(1 for v in dropped_fines.values() if v >= t)
            for t in THRESHOLDS},
        "without_current_dropped_fines_top": [
            {"ccn": c, "dollars": round(dropped_fines[c]),
             "fines": len(dropped_rows[c]),
             "years": min(dropped_rows[c])[:4] + "-" + max(dropped_rows[c])[:4]}
            for c in top],
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    held = cms_ledger.captures()
    if not held:
        raise SystemExit("No capture. Run tools/snapshot_cms.py first.")
    capture = held[-1]
    out = figures(read_history(), capture.facilities())
    out["capture"] = capture.stamp
    if args.json:
        print(json.dumps(out, indent=2, sort_keys=True))
        return
    for key in sorted(out):
        print("%-45s %s" % (key, out[key]))


if __name__ == "__main__":
    main()
