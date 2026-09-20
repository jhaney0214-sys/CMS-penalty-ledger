#!/usr/bin/env python3
"""Write the CMS archive out as the static data the Nursing Home Penalty Ledger page reads.

    python tools/cms_export.py --out CMS-penalty-ledger/docs/data

(from the workstation root; the weekly task runs it after every new capture)

The ledger page is the surface a non-author opens; this is the only place its numbers
come from. Everything is computed through `cms_ledger` - the same loaders, the
same state summary, the same penalty identity - so the page cannot disagree
with the command line, the way Outcrop's page cannot disagree with `look.py`
about the law. The refusal sentences travel in the data for the same reason:
the page prints `NO_PENALTY` from here rather than typing its own.

Output, all JSON:

    meta.json          the capture, CMS's edition, the observed window, what
                       is not held, national and per-state totals
    index.json         every facility: [ccn, name, city, state, penalised]
    states/XX.json     per state, keyed by CCN: surveys, fines, denials, and
                       the penalties CMS has since stopped publishing

**The last of those is the reason the archive exists.** A penalty present in
an earlier capture and absent from the latest has aged out of CMS's rolling
window, and this is the only place it can still be seen. With one capture held
there are none, and `meta.captures` says how many were compared so the page
can say "nothing to compare" rather than "nothing dropped".

Fines and payment denials stay in separate arrays and are never summed.
"""

import argparse
import collections
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import cms_ledger  # noqa: E402

SCHEMA = 1


def dropped_penalties(held):
    """Penalties some earlier capture published and the latest does not.

    Returns {ccn: [penalty dict + "last_seen" stamp]}. Identity is
    `cms_ledger.penalty_key`, so a restated denial reads as dropped here
    exactly as it does in `--changes`.
    """
    if len(held) < 2:
        return {}
    latest = {cms_ledger.penalty_key(p) for p in held[-1].penalties}
    last_seen = {}
    for capture in held[:-1]:
        for pen in capture.penalties:
            key = cms_ledger.penalty_key(pen)
            if key not in latest:
                last_seen[key] = dict(pen, last_seen=capture.stamp)
    out = collections.defaultdict(list)
    for pen in last_seen.values():
        out[pen["ccn"]].append(pen)
    return dict(out)


HISTORY_MANIFEST = os.path.join(os.path.dirname(cms_ledger.HISTORY),
                                "manifest.json")


def history_dropped(path=cms_ledger.HISTORY, manifest=HISTORY_MANIFEST):
    """({ccn: [penalty]}, summary or None) from `backfill_cms.py`'s rebuilt
    history: every penalty some archived CMS edition since 2019-01 published
    and CMS's latest edition does not. `last_seen` is the archive date of the
    last edition that listed it.

    No history built returns ({}, None), and the page then says it cannot
    compare rather than that nothing was dropped.
    """
    if not os.path.isfile(path) or not os.path.isfile(manifest):
        return {}, None
    with open(manifest, encoding="utf-8") as handle:
        summary = json.load(handle)["summary"]
    out = collections.defaultdict(list)
    for r in cms_ledger._rows(path):
        if r["in_latest"] == "yes":
            continue
        out[r["ccn"]].append({
            "ccn": r["ccn"], "name": r["name"], "city": r["city"],
            "state": r["state"], "kind": r["kind"], "date": r["date"],
            "amount": cms_ledger._int(r["amount"]),
            "denial_days": cms_ledger._int(r["denial_days"]),
            "last_seen": r["last_seen"]})
    return dict(out), summary


def _fine(p):
    return [p["date"], p["amount"]]


def _denial(p):
    return [p["date"], p["denial_start"], p["denial_days"]]


def _survey(s):
    return [s["cycle"], s["health_date"], s["fire_date"], s["health"],
            s["fire"], s["abuse"], s["infection"]]


def build(held, history=({}, None)):
    """Returns (meta, index, {state: records}) for the latest capture.

    `history` is `history_dropped()`'s result. Its penalties were dropped
    before this archive's first capture; `dropped_penalties(held)` covers the
    ones dropped since. The two cannot overlap: everything the history marks
    as still published is in the first capture.
    """
    capture = held[-1]
    dropped = collections.defaultdict(list, dropped_penalties(held))
    older, history_summary = history
    for ccn, pens in older.items():
        dropped[ccn].extend(pens)

    by_ccn = {}
    for row in capture.surveys + capture.penalties:
        by_ccn.setdefault(row["ccn"], {
            "name": row["name"], "city": row["city"], "state": row["state"],
            "surveys": [], "fines": [], "denials": [], "dropped": []})
    for s in sorted(capture.surveys, key=lambda s: s["cycle"]):
        by_ccn[s["ccn"]]["surveys"].append(_survey(s))
    for p in sorted(capture.penalties, key=lambda p: p["date"], reverse=True):
        fines, denials = cms_ledger.split([p])
        key = "fines" if fines else "denials" if denials else None
        if key:
            by_ccn[p["ccn"]][key].append(_fine(p) if fines else _denial(p))
    for ccn, pens in dropped.items():
        if ccn not in by_ccn:
            head = pens[0]
            by_ccn[ccn] = {"name": head["name"], "city": head["city"],
                           "state": head["state"], "surveys": [], "fines": [],
                           "denials": [], "dropped": []}
        for p in sorted(pens, key=lambda p: p["date"], reverse=True):
            is_fine = p["kind"] == cms_ledger.FINE
            by_ccn[ccn]["dropped"].append(
                [p["kind"], p["date"],
                 p["amount"] if is_fine else p["denial_days"], p["last_seen"]])

    states = collections.defaultdict(dict)
    index = []
    for ccn in sorted(by_ccn, key=lambda c: (by_ccn[c]["state"],
                                             by_ccn[c]["name"])):
        rec = by_ccn[ccn]
        penalised = 1 if rec["fines"] or rec["denials"] else 0
        index.append([ccn, rec["name"], rec["city"], rec["state"], penalised])
        states[rec["state"]][ccn] = {
            k: rec[k] for k in ("surveys", "fines", "denials", "dropped")}

    state_totals = {}
    for code in sorted({s["state"] for s in capture.surveys}):
        summary = cms_ledger.state_summary(capture, code)
        state_totals[code] = {
            "surveyed": summary["surveyed"],
            "penalised": summary["penalised"],
            "not_penalised": summary["clean"],
            "fines": len(summary["fines"]),
            "fine_total": summary["fine_total"],
            "denials": len(summary["denials"]),
        }

    fines, denials = cms_ledger.split(capture.penalties)
    start, end = capture.window
    meta = {
        "schema": SCHEMA,
        "capture": capture.stamp,
        "captures": [c.stamp for c in held],
        "edition": capture.processing_date,
        "window": [start, end],
        "not_kept": capture.not_kept,
        "surveyed": len(capture.facilities()),
        "penalised": len({p["ccn"] for p in capture.penalties}),
        "fines": len(fines),
        "fine_total": sum(f["amount"] or 0 for f in fines),
        "denials": len(denials),
        "dropped": sum(len(v) for v in dropped.values()),
        "history": ({"editions": history_summary["editions"],
                     "first": history_summary["first_edition"],
                     "last": history_summary["last_edition"]}
                    if history_summary else None),
        "states": state_totals,
        "text": {"no_penalty": cms_ledger.NO_PENALTY,
                 "none_penalised": cms_ledger.NONE_PENALISED,
                 "not_clean": cms_ledger.NOT_CLEAN},
        "survey_columns": ["cycle", "health_date", "fire_date", "health",
                           "fire", "abuse", "infection"],
    }
    return meta, index, dict(states)


def _write(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(data, handle, separators=(",", ":"), ensure_ascii=False,
                  sort_keys=True)
        handle.write("\n")


def export(out, archive=cms_ledger.ARCHIVE):
    held = cms_ledger.captures(archive)
    if not held:
        raise SystemExit("No capture in %s. Run tools/snapshot_cms.py first."
                         % archive)
    # The rebuilt history sits beside the archive it extends, so an archive
    # in a temporary directory never picks up the real one.
    beside = os.path.join(os.path.dirname(os.path.abspath(archive)),
                          "cms-archive")
    meta, index, states = build(held, history_dropped(
        os.path.join(beside, "penalties_history.csv.gz"),
        os.path.join(beside, "manifest.json")))
    _write(os.path.join(out, "meta.json"), meta)
    _write(os.path.join(out, "index.json"), index)
    for code, records in states.items():
        _write(os.path.join(out, "states", "%s.json" % code), records)
    return meta


def main(argv):
    ap = argparse.ArgumentParser(description="Export the CMS archive for the Nursing Home Penalty Ledger")
    ap.add_argument("--out", required=True, help="the ledger's docs/data directory")
    args = ap.parse_args(argv[1:])
    meta = export(args.out)
    print("capture %s (CMS edition %s): %d facilities, %d penalised, "
          "%d fines, %d denials, %d dropped across %d capture(s) -> %s"
          % (meta["capture"], meta["edition"], meta["surveyed"],
             meta["penalised"], meta["fines"], meta["denials"],
             meta["dropped"], len(meta["captures"]), args.out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
