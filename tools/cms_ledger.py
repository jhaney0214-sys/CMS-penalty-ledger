#!/usr/bin/env python3
"""Read the CMS archive: what is on record against a facility or a state.

    python tools/cms_ledger.py --captures           # what the archive holds
    python tools/cms_ledger.py --state IL           # a state, with denominator
    python tools/cms_ledger.py --ccn 015019         # one facility's record
    python tools/cms_ledger.py --name "merry wood"  # find a facility by name
    python tools/cms_ledger.py --changes            # what moved between two
    python tools/cms_ledger.py --history 015019     # every edition since 2019

`snapshot_cms.py` keeps the files; this reads them. Nothing here touches the
network, so a report is a function of a dated directory and can be re-run
years later against the same bytes and give the same answer.

## The three things this refuses to say

**An empty record is not a clean record.** 7,915 of the 14,690 facilities in
the 2026-09-18 capture have no penalty row, and the honest reading of that is
"CMS published no penalty for this facility inside this window", not "this
facility is fine". Every report says so in those words rather than printing a
zero and leaving the inference to the reader. This is the failure mode the
workstation has recorded five times over - an instrument that could not look
reporting the same as one that looked and found nothing.

**The window is the file's, not the world's.** CMS publishes penalties as a
rolling three-year window, so the earliest penalty date in a capture is an
artefact of when the file was made. Every report prints the window it actually
observed, read off the rows rather than hardcoded, so nobody reads the start of
the data as the start of the facility's history.

**Deficiency detail is not held.** The Health and Fire Safety citation files
are 165 MB and 66 MB a month and the archive records them by manifest only, so
this tool can report deficiency *counts by category*, from the survey summary,
and can never report which tag was cited. `citation_codes` is archived as the
lookup those files would join to, and in this archive it joins to nothing -
which is worth stating plainly rather than letting a reader assume the tag
detail is somewhere in here.

## Fines and payment denials are never added together

They are different penalties in different units - 13,256 fines totalling
$456,752,787, and 2,440 denials of payment for new admissions measured in days.
A single "penalty total" would have to pick one or silently drop the other, so
every report carries both and sums neither into the other.

## Identity across captures, and where it is weaker than it looks

`--changes` compares two captures row by row. A fine carries a `Fine ID` that
is unique within a capture (13,256 ids for 13,256 fines), so a fine is tracked
exactly. **A payment denial carries no id at all** - every one of the 2,440 is
blank - so denials are keyed on CCN, date, start date and length together. That
key is unique within the capture, but if CMS ever restates a denial's length
the diff reads it as one row dropped and a different one added. Reported as a
caveat on the change report rather than hidden, because the alternative is a
tool that quietly claims a restatement is a deletion.

**What dropping means is the whole point of the archive.** A row in an earlier
capture and absent from a later one has usually aged out of the three-year
window, and CMS cannot give it back. This is the only place that difference is
observable, and it needs at least two captures to exist - with one, the change
report says so rather than printing an empty diff that looks like stability.
"""

import argparse
import collections
import csv
import gzip
import io
import json
import os
import statistics
import sys
import textwrap

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ARCHIVE = os.path.join(HERE, "snapshots", "cms")
HISTORY = os.path.join(HERE, "snapshots", "cms-archive",
                       "penalties_history.csv.gz")

FINE = "Fine"
DENIAL = "Payment Denial"

# Said in full, every time, rather than printed as a zero. One constant, so
# the facility report and the state report cannot drift apart on the one
# claim this tool most needs to get right.
NOT_CLEAN = "That is not a finding of good care."
NO_PENALTY = ("CMS published no penalty for this facility inside this "
              "capture's window. " + NOT_CLEAN)
NONE_PENALISED = ("CMS published no penalty for them inside this capture's "
                  "window. " + NOT_CLEAN)


def _wrap(text, indent="  "):
    return textwrap.fill(text, width=76, initial_indent=indent,
                         subsequent_indent=indent)


def _rows(path):
    """Every archived file is gzipped UTF-8 CSV with a header row."""
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        return list(csv.DictReader(io.StringIO(handle.read())))


def _int(value):
    value = (value or "").strip()
    return int(value) if value else None


def normalise_penalty(row):
    return {
        "ccn": row["CMS Certification Number (CCN)"],
        "name": row["Provider Name"],
        "address": row["Provider Address"],
        "city": row["City/Town"],
        "state": row["State"],
        "zip": row["ZIP Code"],
        "date": row["Penalty Date"],
        "kind": row["Penalty Type"],
        "fine_id": (row["Fine ID"] or "").strip(),
        "amount": _int(row["Fine Amount"]),
        "denial_start": (row["Payment Denial Start Date"] or "").strip(),
        "denial_days": _int(row["Payment Denial Length in Days"]),
        "processing_date": row["Processing Date"],
    }


def normalise_survey(row):
    return {
        "ccn": row["CMS Certification Number (CCN)"],
        "name": row["Provider Name"],
        "city": row["City/Town"],
        "state": row["State"],
        "cycle": row["Inspection Cycle"],
        "health_date": row["Health Survey Date"],
        "fire_date": row["Fire Safety Survey Date"],
        "health": _int(row["Total Number of Health Deficiencies"]),
        "fire": _int(row["Total Number of Fire Safety Deficiencies"]),
        "abuse": _int(row["Count of Freedom from Abuse and Neglect and "
                          "Exploitation Deficiencies"]),
        "infection": _int(row["Count of Infection Control Deficiencies"]),
    }


def penalty_key(pen):
    """Stable identity across captures.

    A fine's id is enough on its own. A denial has none, so the key is
    everything that describes it - see the module docstring for what that
    costs when CMS restates one.
    """
    if pen["fine_id"]:
        return ("fine", pen["fine_id"])
    return ("denial", pen["ccn"], pen["date"], pen["denial_start"],
            pen["denial_days"])


class Capture(object):
    """One dated directory, read lazily so --captures stays instant."""

    def __init__(self, stamp, path):
        self.stamp = stamp
        self.path = path
        self._penalties = None
        self._surveys = None
        self._manifest = None

    @property
    def penalties(self):
        if self._penalties is None:
            self._penalties = [normalise_penalty(r) for r in
                               _rows(os.path.join(self.path,
                                                  "penalties.csv.gz"))]
        return self._penalties

    @property
    def surveys(self):
        if self._surveys is None:
            self._surveys = [normalise_survey(r) for r in
                             _rows(os.path.join(self.path,
                                                "survey_summary.csv.gz"))]
        return self._surveys

    @property
    def manifest(self):
        if self._manifest is None:
            path = os.path.join(self.path, "manifest.json")
            if os.path.isfile(path):
                with open(path, encoding="utf-8") as handle:
                    self._manifest = json.load(handle)
            else:
                self._manifest = {}
        return self._manifest

    @property
    def not_kept(self):
        """Files CMS published on this date whose bytes the archive does not
        hold. Named so a reader can prove what is missing rather than guess."""
        return [f["title"] for f in self.manifest.get("files", [])
                if not f.get("archived")]

    @property
    def processing_date(self):
        """CMS's own edition stamp, which is not the capture date."""
        dates = {p["processing_date"] for p in self.penalties}
        return sorted(dates)[-1] if dates else ""

    @property
    def window(self):
        dates = sorted(p["date"] for p in self.penalties if p["date"])
        return (dates[0], dates[-1]) if dates else ("", "")

    def facilities(self):
        """Every facility CMS surveyed - a penalty count needs the
        denominator to mean anything."""
        return {s["ccn"] for s in self.surveys}


def captures(archive=ARCHIVE):
    if not os.path.isdir(archive):
        return []
    out = []
    for stamp in sorted(os.listdir(archive)):
        path = os.path.join(archive, stamp)
        if os.path.isdir(path) and os.path.isfile(
                os.path.join(path, "penalties.csv.gz")):
            out.append(Capture(stamp, path))
    return out


def load(stamp=None, archive=ARCHIVE):
    held = captures(archive)
    if not held:
        raise SystemExit("No capture in %s. Run tools/snapshot_cms.py first."
                         % os.path.relpath(archive, HERE))
    if stamp is None:
        return held[-1]
    for capture in held:
        if capture.stamp == stamp:
            return capture
    raise SystemExit("No capture %s. Held: %s"
                     % (stamp, ", ".join(c.stamp for c in held)))


def normalise_ccn(text):
    """CCNs are six characters and lead with zeros that a spreadsheet eats."""
    text = (text or "").strip()
    return text.zfill(6) if text.isdigit() else text.upper()


def facility(capture, ccn):
    ccn = normalise_ccn(ccn)
    pens = [p for p in capture.penalties if p["ccn"] == ccn]
    surveys = sorted((s for s in capture.surveys if s["ccn"] == ccn),
                     key=lambda s: s["cycle"])
    if not pens and not surveys:
        return None
    head = (pens or surveys)[0]
    return {"ccn": ccn, "name": head["name"], "state": head["state"],
            "city": head["city"], "penalties": pens, "surveys": surveys}


def search(capture, text, state=None):
    text = text.lower()
    seen = {}
    for row in capture.surveys:
        if text in row["name"].lower() and (not state
                                            or row["state"] == state.upper()):
            seen.setdefault(row["ccn"], row)
    return sorted(seen.values(), key=lambda r: (r["state"], r["name"]))


def _money(value):
    return "$%s" % format(int(value), ",d")


def split(pens):
    fines = [p for p in pens if p["kind"] == FINE]
    denials = [p for p in pens if p["kind"] == DENIAL]
    return fines, denials


def state_summary(capture, state):
    state = state.upper()
    surveyed = {s["ccn"] for s in capture.surveys if s["state"] == state}
    pens = [p for p in capture.penalties if p["state"] == state]
    fines, denials = split(pens)
    penalised = {p["ccn"] for p in pens}
    totals = collections.Counter()
    for fine in fines:
        totals[fine["ccn"]] += fine["amount"] or 0
    return {
        "state": state,
        "surveyed": len(surveyed),
        "penalised": len(penalised),
        "clean": len(surveyed - penalised),
        "fines": fines,
        "denials": denials,
        "fine_total": sum(f["amount"] or 0 for f in fines),
        "per_facility": totals,
    }


def changes(earlier, later):
    """What CMS added, and what it stopped publishing.

    The second half is the archive's reason to exist: a dropped row is one
    the rolling window has aged out, and no later fetch can recover it.
    """
    before = {penalty_key(p): p for p in earlier.penalties}
    after = {penalty_key(p): p for p in later.penalties}
    return {
        "added": [after[k] for k in after if k not in before],
        "dropped": [before[k] for k in before if k not in after],
        "earlier": earlier.stamp,
        "later": later.stamp,
    }


# ----------------------------------------------------------------- history

def history_for(ccn, path=HISTORY):
    """Every penalty CMS has published against one facility since the
    earliest archived edition, from `backfill_cms.py`'s rebuilt history.
    None when no history has been built, which is not the same as none."""
    if not os.path.isfile(path):
        return None
    ccn = normalise_ccn(ccn)
    return [r for r in _rows(path) if r["ccn"] == ccn]


def history_report(ccn, rows):
    ccn = normalise_ccn(ccn)
    if rows is None:
        return ("No rebuilt history. Run tools/backfill_cms.py first. Without "
                "it only CMS's current window can be read, with --ccn.")
    if not rows:
        return _wrap("CMS published no penalty for CCN %s in any archived "
                     "edition since 2019-01. %s" % (ccn, NOT_CLEAN))
    latest = rows[-1]
    fines = [r for r in rows if r["kind"] == FINE]
    denials = [r for r in rows if r["kind"] == DENIAL]
    gone = [r for r in rows if r["in_latest"] != "yes"]
    lines = ["%s  (CCN %s, %s, %s)" % (latest["name"], ccn, latest["city"],
                                       latest["state"]), "",
             "  Every penalty in CMS's archived editions since 2019-01:",
             "  %d fine(s) totalling %s; %d payment denial(s). Never added "
             "together." % (len(fines), _money(sum(_int(f["amount"]) or 0
                                                    for f in fines)),
                             len(denials)),
             "  %d of these %s no longer in CMS's current file and %s shown "
             "only here." % (len(gone), "is" if len(gone) == 1 else "are",
                             "is" if len(gone) == 1 else "are"), ""]
    for r in sorted(rows, key=lambda r: (r["date"], r["kind"])):
        what = ("fine           %12s" % _money(_int(r["amount"]) or 0)
                if r["kind"] == FINE else
                "payment denial %8s days" % (r["denial_days"] or "?"))
        seen = ("published %s .. %s" % (r["first_seen"], r["last_seen"])
                if r["in_latest"] != "yes" else
                "still published (since %s)" % r["first_seen"])
        revised = ""
        if r.get("first_amount") and r["kind"] == FINE:
            revised = "  [first listed at %s]" % _money(_int(r["first_amount"]))
        elif r.get("first_denial_days") and r["kind"] == DENIAL:
            revised = "  [first listed at %s days]" % r["first_denial_days"]
        lines.append("    %s  %s  %s%s%s" % (r["date"], what, seen, revised,
                                             "  [left and came back]"
                                             if r["gaps"] != "0" else ""))
    lines += ["",
              _wrap("A penalty no longer published has aged out of CMS's "
                    "window. That is not a reversal, and the file never says "
                    "one was. Dates before 2016 are outside every archived "
                    "edition, so a clean record before then is unknown, not "
                    "clean.")]
    return "\n".join(lines)


# ----------------------------------------------------------------- reports

def _provenance(capture):
    start, end = capture.window
    lines = [
        "  capture %s, CMS edition %s"
        % (capture.stamp, capture.processing_date or "unknown"),
        "  penalty dates run %s to %s. The start of that range is the"
        % (start, end),
        "  rolling window opening, not the start of anyone's history.",
    ]
    if capture.not_kept:
        lines.append("  not held in this archive: %s"
                     % ", ".join(capture.not_kept))
        lines.append("  (manifest only - too large for git; no citation "
                     "detail is available here).")
    return lines


def captures_report(held):
    if not held:
        return "No capture held. Run tools/snapshot_cms.py."
    lines = ["CMS ARCHIVE - %d capture(s)" % len(held), ""]
    for capture in held:
        start, end = capture.window
        lines.append("  %s  %6d penalties, %5d facilities, window %s..%s"
                     % (capture.stamp, len(capture.penalties),
                        len(capture.facilities()), start, end))
    lines += [""]
    if len(held) == 1:
        lines += ["  One capture, so nothing can be compared yet. A "
                  "trajectory needs two,",
                  "  and CMS drops rows past three years - so the months "
                  "between captures",
                  "  are the months this archive can never recover."]
    else:
        lines += ["  Compare with --changes."]
    return "\n".join(lines)


def facility_report(capture, record):
    fines, denials = split(record["penalties"])
    lines = ["%s  (CCN %s, %s, %s)" % (record["name"], record["ccn"],
                                       record["city"], record["state"]), ""]
    if not record["penalties"]:
        lines += [_wrap(NO_PENALTY), ""]
    else:
        lines.append("  %d fine(s) totalling %s; %d payment denial(s). The "
                     "two are not"
                     % (len(fines),
                        _money(sum(f["amount"] or 0 for f in fines)),
                        len(denials)))
        lines.append("  added together - one is money, the other is days of "
                     "denied payment.")
        lines.append("")
        for pen in sorted(record["penalties"], key=lambda p: p["date"]):
            if pen["kind"] == FINE:
                lines.append("    %s  fine           %12s  (id %s)"
                             % (pen["date"], _money(pen["amount"] or 0),
                                pen["fine_id"]))
            else:
                lines.append("    %s  payment denial %8s days  (from %s)"
                             % (pen["date"], pen["denial_days"],
                                pen["denial_start"] or "unstated"))
        lines.append("")
        summary = state_summary(capture, record["state"])
        mine = summary["per_facility"].get(record["ccn"], 0)
        others = sorted(v for v in summary["per_facility"].values() if v)
        if others and mine:
            rank = sum(1 for v in others if v > mine) + 1
            lines.append("  Against %s: %s in fines ranks %d of %d facilities "
                         "with any fine." % (record["state"], _money(mine),
                                             rank, len(others)))
            lines.append("  Median fined facility there: %s."
                         % _money(statistics.median(others)))
            lines.append("")
    if record["surveys"]:
        lines.append("  Inspection cycles (counts only - this archive holds "
                     "no citation detail):")
        for survey in record["surveys"]:
            lines.append("    cycle %s  %s   health %3s   fire %3s   abuse %2s"
                         "   infection %2s"
                         % (survey["cycle"],
                            survey["health_date"] or "no date",
                            survey["health"], survey["fire"], survey["abuse"],
                            survey["infection"]))
        lines.append("")
    else:
        lines += ["  No survey summary row, which means CMS did not publish",
                  "  an inspection history for this CCN in this capture.", ""]
    return "\n".join(lines + _provenance(capture))


def state_report(capture, state):
    s = state_summary(capture, state)
    if not s["surveyed"]:
        return ("No facility in %s in the %s capture. State codes are CMS's "
                "two-letter codes." % (s["state"], capture.stamp))
    fines, denials = s["fines"], s["denials"]
    amounts = [f["amount"] for f in fines if f["amount"] is not None]
    days = [d["denial_days"] for d in denials if d["denial_days"] is not None]
    lines = ["%s - CMS nursing-home penalties, capture %s"
             % (s["state"], capture.stamp), "",
             "  %d facilities surveyed; %d carry at least one penalty "
             "(%.0f%%)." % (s["surveyed"], s["penalised"],
                            100.0 * s["penalised"] / s["surveyed"]),
             _wrap("The other %d have no penalty row. %s"
                   % (s["clean"], NONE_PENALISED)),
             ""]
    if amounts:
        lines += ["  %d fines, %s total, median %s, largest %s."
                  % (len(amounts), _money(sum(amounts)),
                     _money(statistics.median(amounts)), _money(max(amounts))),
                  ]
    else:
        lines.append("  No fine recorded in this window.")
    if days:
        lines.append("  %d payment denials, median %d days, longest %d days."
                     % (len(days), statistics.median(days), max(days)))
    else:
        lines.append("  No payment denial recorded in this window.")
    if s["per_facility"]:
        lines += ["", "  Largest fine totals by facility:"]
        ranked = sorted(s["per_facility"].items(), key=lambda kv: -kv[1])[:10]
        names = {p["ccn"]: p["name"] for p in capture.penalties}
        for ccn, total in ranked:
            lines.append("    %12s  %s  (CCN %s)"
                         % (_money(total), names.get(ccn, "?"), ccn))
    lines.append("")
    return "\n".join(lines + _provenance(capture))


def changes_report(held):
    if len(held) < 2:
        return "\n".join([
            "One capture held (%s), so there is nothing to compare."
            % (held[0].stamp if held else "none"),
            "",
            "  This is not 'no change'. CMS refreshes monthly and drops rows "
            "past three",
            "  years, so a second capture is what makes any of it visible. "
            "Run",
            "  tools/snapshot_cms.py after the next publication.",
        ])
    diff = changes(held[-2], held[-1])
    added_f, added_d = split(diff["added"])
    dropped_f, dropped_d = split(diff["dropped"])
    lines = ["CMS PENALTIES: %s to %s" % (diff["earlier"], diff["later"]), "",
             "  added:   %5d rows (%d fines, %s; %d denials)"
             % (len(diff["added"]), len(added_f),
                _money(sum(f["amount"] or 0 for f in added_f)), len(added_d)),
             "  dropped: %5d rows (%d fines, %s; %d denials)"
             % (len(diff["dropped"]), len(dropped_f),
                _money(sum(f["amount"] or 0 for f in dropped_f)),
                len(dropped_d)),
             ""]
    if diff["dropped"]:
        lines += ["  A dropped row has aged out of CMS's three-year window. "
                  "This archive is",
                  "  the only place it still exists; no later fetch can bring "
                  "it back.", ""]
        by_state = collections.Counter(p["state"] for p in diff["dropped"])
        lines.append("  dropped by state: " + ", ".join(
            "%s %d" % (st, n) for st, n in by_state.most_common(8)))
        lines.append("")
    lines += ["  Fines are matched on CMS's own Fine ID. Denials carry no id, "
              "so they are",
              "  matched on CCN, date, start and length together - a restated "
              "denial reads",
              "  here as one dropped and one added rather than as an "
              "amendment."]
    return "\n".join(lines)


def main(argv):
    ap = argparse.ArgumentParser(
        description="Report on the archived CMS nursing-home files")
    ap.add_argument("--captures", action="store_true",
                    help="what the archive holds")
    ap.add_argument("--changes", action="store_true",
                    help="what moved between the two most recent captures")
    ap.add_argument("--state", help="two-letter state code")
    ap.add_argument("--ccn", help="a facility's CMS Certification Number")
    ap.add_argument("--name", help="find a facility by name")
    ap.add_argument("--capture", help="read a specific dated capture")
    ap.add_argument("--history", metavar="CCN",
                    help="a facility's penalties across every archived "
                         "CMS edition since 2019")
    args = ap.parse_args(argv[1:])

    if args.history:
        print(history_report(args.history, history_for(args.history)))
        return 0

    if args.captures:
        print(captures_report(captures()))
        return 0
    if args.changes:
        print(changes_report(captures()))
        return 0

    capture = load(args.capture)
    if args.ccn:
        record = facility(capture, args.ccn)
        if record is None:
            print("No facility with CCN %s in the %s capture. CCNs are six "
                  "characters." % (normalise_ccn(args.ccn), capture.stamp))
            return 1
        print(facility_report(capture, record))
        older = history_for(args.ccn)
        gone = [r for r in older or [] if r["in_latest"] != "yes"]
        if gone:
            print("\n  %d earlier penalt%s CMS no longer publishes %s held in "
                  "the rebuilt history: --history %s"
                  % (len(gone), "y" if len(gone) == 1 else "ies",
                     "is" if len(gone) == 1 else "are", record["ccn"]))
        return 0
    if args.name:
        hits = search(capture, args.name, args.state)
        if not hits:
            print("No facility matching %r%s in the %s capture."
                  % (args.name, " in " + args.state.upper() if args.state
                     else "", capture.stamp))
            return 1
        for hit in hits[:25]:
            print("  %s  %s, %s  (CCN %s)"
                  % (hit["state"], hit["name"], hit["city"], hit["ccn"]))
        if len(hits) > 25:
            print("  ... %d more" % (len(hits) - 25))
        return 0
    if args.state:
        print(state_report(capture, args.state))
        return 0

    ap.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
