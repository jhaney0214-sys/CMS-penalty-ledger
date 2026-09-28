#!/usr/bin/env python3
"""Rebuild nursing-home penalty history from CMS's own monthly archive.

    python tools/backfill_cms.py            # fetch what is missing, rebuild
    python tools/backfill_cms.py --offline  # rebuild from the local cache only
    python tools/backfill_cms.py --report   # summarise what was built

## Why this exists

`snapshot_cms.py` was written on the belief that history older than CMS's
rolling window could not be recovered, because the archive page is a
JavaScript application and a plain fetch returns no file list. That was
never looked at in a browser. Loaded, the page calls LISTING below, which
names 97 public ZIPs from 2019-01-17 onward. 86 of them carry a penalties
file. CMS's archived August 2026 file is byte-identical to this
workstation's own 2026-09-18 capture (sha256 3683f1de...), so the archive is
the record, not an approximation of it.

## What it takes, and how little

Each ZIP is about 38 MB, and only the penalties member is wanted (1-7 MB).
The ZIP's central directory is read with an HTTP range request and only that
member's bytes are fetched, so the whole backfill moves about 350 MB rather
than 3.6 GB. Raw members are cached in `data/cms-archive/`, which is not
committed. They can be fetched again from CMS at any time, and the manifest
records each one's sha256 so a refetch can be proven identical.

## Traps, each found in the real archive

**Four header eras.** Before 2020-08 the columns are `provnum`, `pnlty_date`,
`fine_amt`... From 2020-08 they are `Federal Provider Number`, `Provider
City`... The current era uses `CMS Certification Number (CCN)` and adds
`Fine ID`. An unrecognised header is an error, never a best guess.

**Fine ID exists only in the current era.** So a penalty cannot be followed
across editions by id. It is keyed on facility, date, type, amount and the
two denial fields instead. That key is NOT unique: in the August 2026 file,
288 rows share a key with another row and carry a different Fine ID. So the
history counts occurrences per edition (a multiset). The second identical
fine on a day is its own penalty, not a duplicate to collapse.

**An edition is sometimes re-archived unchanged.** Nov 2020, Oct 2021, Oct
2022, Oct 2023 and Oct 2024 each appear under two archive dates. The sha256
decides: an identical member is recorded as a duplicate and not counted
twice.

**Not every ZIP is an edition.** The year-end aggregates carry no penalties
file, one ZIP carries `__MACOSX/` resource forks beside the real member, and
member paths change three times. The member is chosen by name, ignoring junk.

**The window is not a fixed three years.** In December 2021 fines rose from
14,003 to 24,230 over nearly the same date range, and January 2023 held
36,516 rows against 15,696 today. CMS changed what it publishes as well as
when. So "first seen" and "last seen" are facts about CMS's files, and this
tool never reads a penalty's absence as its reversal.

**A changed amount looks like one penalty dropped and another added.** CMS
revises penalties in place, often by 35%, and keyed on the amount the old
figure would read as "no longer published" while CMS still publishes the new
one. Versions at one facility, date and type are linked when exactly one ends
as exactly one begins; see `link_restatements`. The first amount is kept.
"""

import argparse
import collections
import csv
import gzip
import hashlib
import io
import json
import os
import struct
import sys
import time
import urllib.request
import zlib

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(HERE, "snapshots", "cms-archive")
CACHE = os.path.join(HERE, "data", "cms-archive")
SNAPSHOTS = os.path.join(HERE, "snapshots", "cms")
#: Where a capture's file can be read by anyone checking a page's citation.
REPO_FILES = "https://github.com/jhaney0214-sys/cms-penalty-ledger/blob/main/"

BASE = "https://data.cms.gov"
LISTING = (BASE + "/provider-data/api/1/archive/aggregate/theme/"
           "nursing-homes/relative")
AGENT = "ai-workstation-cms-backfill/1.0"
PAUSE = 0.5                          # between requests; CMS is not a CDN test

# Header eras. Each maps the file's own column name to the reader's field.
ERAS = {
    "2019": {
        "provnum": "ccn", "provname": "name", "address": "address",
        "city": "city", "state": "state", "zip": "zip",
        "pnlty_date": "date", "pnlty_type": "kind", "fine_amt": "amount",
        "payden_strt_dt": "denial_start", "payden_days": "denial_days",
        "filedate": "processing_date",
    },
    "2020": {
        "Federal Provider Number": "ccn", "Provider Name": "name",
        "Provider Address": "address", "Provider City": "city",
        "Provider State": "state", "Provider Zip Code": "zip",
        "Penalty Date": "date", "Penalty Type": "kind",
        "Fine Amount": "amount", "Payment Denial Start Date": "denial_start",
        "Payment Denial Length in Days": "denial_days",
        "Location": None, "Processing Date": "processing_date",
    },
    "ccn": {                          # current names, before Fine ID existed
        "CMS Certification Number (CCN)": "ccn", "Provider Name": "name",
        "Provider Address": "address", "City/Town": "city", "State": "state",
        "ZIP Code": "zip", "Penalty Date": "date", "Penalty Type": "kind",
        "Fine Amount": "amount",
        "Payment Denial Start Date": "denial_start",
        "Payment Denial Length in Days": "denial_days",
        "Location": None, "Processing Date": "processing_date",
    },
    "current": {
        "CMS Certification Number (CCN)": "ccn", "Provider Name": "name",
        "Provider Address": "address", "City/Town": "city", "State": "state",
        "ZIP Code": "zip", "Penalty Date": "date", "Penalty Type": "kind",
        "Fine ID": "fine_id", "Fine Amount": "amount",
        "Payment Denial Start Date": "denial_start",
        "Payment Denial Length in Days": "denial_days",
        "Location": None, "Processing Date": "processing_date",
    },
}

FIELDS = ("ccn", "name", "address", "city", "state", "zip", "date", "kind",
          "amount", "denial_start", "denial_days")
KEY = ("ccn", "date", "kind", "amount", "denial_start", "denial_days")


# ------------------------------------------------------------ remote zip

def http_range(url, start, end=None):
    """(bytes, total size). start < 0 means the last -start bytes."""
    spec = ("bytes=%d" % start) if start < 0 else (
        "bytes=%d-%s" % (start, "" if end is None else end))
    req = urllib.request.Request(url, headers={"User-Agent": AGENT,
                                               "Range": spec})
    with urllib.request.urlopen(req, timeout=180) as resp:
        body = resp.read()
        rng = resp.headers.get("Content-Range")
    time.sleep(PAUSE)
    if not rng:
        raise IOError("%s ignored the Range header" % url[-60:])
    return body, int(rng.rsplit("/", 1)[1])


def zip_directory(url, fetch=http_range):
    """Every member's name, method, sizes and offset, from the central
    directory alone. Handles zip64, which the 622 MB July 2026 ZIP needs."""
    tail, _ = fetch(url, -262144)
    i = tail.rfind(b"PK\x05\x06")
    if i < 0:
        raise ValueError("no end-of-central-directory record")
    size, offset = struct.unpack("<II", tail[i + 12:i + 20])
    if 0xFFFFFFFF in (size, offset):
        j = tail.rfind(b"PK\x06\x06")
        size, offset = struct.unpack("<QQ", tail[j + 40:j + 56])
    cd, _ = fetch(url, offset, offset + size - 1)
    out, p = [], 0
    while cd[p:p + 4] == b"PK\x01\x02":
        method, = struct.unpack("<H", cd[p + 10:p + 12])
        csize, usize = struct.unpack("<II", cd[p + 20:p + 28])
        nlen, elen, clen = struct.unpack("<HHH", cd[p + 28:p + 34])
        loff, = struct.unpack("<I", cd[p + 42:p + 46])
        name = cd[p + 46:p + 46 + nlen].decode("utf-8", "replace")
        extra = cd[p + 46 + nlen:p + 46 + nlen + elen]
        q = 0
        while q + 4 <= len(extra):
            hid, hlen = struct.unpack("<HH", extra[q:q + 4])
            body, k = extra[q + 4:q + 4 + hlen], 0
            if hid == 1:                               # zip64 sizes/offset
                if usize == 0xFFFFFFFF:
                    usize, = struct.unpack("<Q", body[k:k + 8]); k += 8
                if csize == 0xFFFFFFFF:
                    csize, = struct.unpack("<Q", body[k:k + 8]); k += 8
                if loff == 0xFFFFFFFF:
                    loff, = struct.unpack("<Q", body[k:k + 8]); k += 8
            q += 4 + hlen
        out.append({"name": name, "method": method, "csize": csize,
                    "usize": usize, "offset": loff})
        p += 46 + nlen + elen + clen
    return out


def zip_member(url, m, fetch=http_range):
    head, _ = fetch(url, m["offset"], m["offset"] + 29)
    nlen, elen = struct.unpack("<HH", head[26:30])
    start = m["offset"] + 30 + nlen + elen
    data, _ = fetch(url, start, start + m["csize"] - 1)
    if m["method"] == 0:
        body = data
    elif m["method"] == 8:
        body = zlib.decompress(data, -15)
    else:
        raise ValueError("compression method %d" % m["method"])
    if len(body) != m["usize"]:
        raise ValueError("%s: %d bytes, directory says %d"
                         % (m["name"], len(body), m["usize"]))
    return body


def penalty_member(members):
    """The penalties file, ignoring macOS resource forks. None if absent."""
    hits = [m for m in members
            if "penalt" in m["name"].rsplit("/", 1)[-1].lower()
            and "__MACOSX" not in m["name"]
            and not m["name"].rsplit("/", 1)[-1].startswith("._")]
    if len(hits) > 1:
        raise ValueError("more than one penalties member: %s"
                         % [m["name"] for m in hits])
    return hits[0] if hits else None


# ------------------------------------------------------------ parsing

def decode(raw):
    """UTF-8 when it is; Latin-1 only when it is not, and said so."""
    try:
        return raw.decode("utf-8-sig"), "utf-8"
    except UnicodeDecodeError:
        return raw.decode("latin-1"), "latin-1"


def era_of(header):
    for name, mapping in ERAS.items():
        if set(header) == set(mapping):
            return name
    raise ValueError("unrecognised penalties header: %s" % header)


def _clean(value):
    return " ".join((value or "").split())


def normalise(raw):
    """(era, encoding, [row dict with FIELDS + fine_id + processing_date])."""
    text, encoding = decode(raw)
    reader = csv.reader(io.StringIO(text))
    header = [h.strip() for h in next(reader)]
    era = era_of(header)
    mapping = ERAS[era]
    rows = []
    for values in reader:
        if not any(v.strip() for v in values):
            continue
        rec = dict.fromkeys(FIELDS + ("fine_id", "processing_date"), "")
        for col, value in zip(header, values):
            field = mapping.get(col)
            if field:
                rec[field] = _clean(value)
        rows.append(rec)
    return era, encoding, rows


def key_of(row):
    return tuple(row[k] for k in KEY)


# ------------------------------------------------------------ history

def build_history(editions):
    """editions: [(date, rows)] oldest first, duplicates already removed.

    Returns {(key, n): record}, where n counts identical keys within one
    edition - the multiset that keeps two same-day fines as two penalties.
    """
    history = {}
    order = [date for date, _ in editions]
    for index, (date, rows) in enumerate(editions):
        seen = collections.Counter()
        for row in rows:
            k = key_of(row)
            n = seen[k]
            seen[k] += 1
            rec = history.get((k, n))
            if rec is None:
                rec = history[(k, n)] = {
                    "first_seen": date, "first_index": index,
                    "appearances": 0, "gaps": 0}
            elif rec["last_index"] != index - 1:
                rec["gaps"] += 1                     # left, then came back
            rec["appearances"] += 1
            rec["last_seen"] = date
            rec["last_index"] = index
            for f in FIELDS:
                rec[f] = row[f]
            if row["fine_id"]:
                rec["fine_id"] = row["fine_id"]
    latest = len(order) - 1
    for rec in history.values():
        rec["in_latest"] = rec["last_index"] == latest
    return history


def link_restatements(history):
    """One record per penalty, following an amount that changed while listed.

    CMS revises a penalty in place and the file shows only the new figure: on
    a 2023-05-30 edition fine 106114 / 2022-12-16 reads $186,212 where the
    month before it read $286,480, a 35% cut. Keyed on the amount, that is
    one penalty dropped and another added, and the page would then list the
    old figure as "no longer published" while CMS still publishes the new
    one. 30,777 facility-date groups in the archive have more than one
    version.

    Two versions are linked only when it is unambiguous: at one facility,
    date and type, exactly one version ends in the edition before exactly
    one other begins. Anything else stays separate and is counted. The
    first amount is kept beside the current one, because "amount changed
    while listed" is what the files show, and why it changed they do not.

    Returns (penalties, linked, ambiguous).
    """
    by = collections.defaultdict(list)
    for rec in history.values():
        by[(rec["ccn"], rec["date"], rec["kind"])].append(rec)
    out, linked, ambiguous = [], 0, 0
    for recs in by.values():
        recs.sort(key=lambda r: (r["first_index"], r["last_index"]))
        ends = collections.defaultdict(list)
        starts = collections.defaultdict(list)
        for r in recs:
            ends[r["last_index"]].append(r)
            starts[r["first_index"]].append(r)
        successor = {}
        for r in recs:
            before = ends.get(r["first_index"] - 1, [])
            peers = starts[r["first_index"]]
            if len(before) == 1 and len(peers) == 1 and before[0] is not r:
                successor[id(before[0])] = r
            elif before:
                ambiguous += 1
        has_pred = {id(r) for r in successor.values()}
        for r in recs:
            if id(r) in has_pred:
                continue
            chain = [r]
            while id(chain[-1]) in successor:
                chain.append(successor[id(chain[-1])])
            head, last = chain[0], chain[-1]
            merged = dict(last)
            merged.update(
                first_seen=head["first_seen"], first_index=head["first_index"],
                appearances=sum(c["appearances"] for c in chain),
                gaps=sum(c["gaps"] for c in chain),
                first_amount=head["amount"] if len(chain) > 1 else "",
                first_denial_days=head["denial_days"] if len(chain) > 1 else "",
                revisions=len(chain) - 1)
            linked += len(chain) - 1
            out.append(merged)
    return out, linked, ambiguous


HISTORY_COLUMNS = ("ccn", "name", "address", "city", "state", "zip", "date",
                   "kind", "fine_id", "amount", "denial_start", "denial_days",
                   "first_amount", "first_denial_days", "revisions",
                   "first_seen", "last_seen", "appearances", "gaps",
                   "in_latest")


def write_history(penalties, path):
    recs = sorted(penalties, key=lambda r: (r["ccn"], r["date"], r["kind"],
                                            r["amount"], r["first_seen"]))
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(HISTORY_COLUMNS)
    for r in recs:
        w.writerow([r.get(c, "") if c != "in_latest" else
                    ("yes" if r["in_latest"] else "no")
                    for c in HISTORY_COLUMNS])
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "wb") as fh, gzip.GzipFile(fileobj=fh, mode="wb",
                                              mtime=0) as gz:
        gz.write(buf.getvalue().encode("utf-8"))
    os.replace(tmp, path)
    return len(recs)


# ------------------------------------------------------------ the run

def _cache_prefix(e):
    # Keyed by archive id as well as date: 2026-08-26 has two archives, one
    # with a penalties file and one without, and a date alone would hand the
    # second the first one's bytes.
    return os.path.join(CACHE, "%s__%s__" % (e["date"], e["id"]))


NONE_MARKER = "NONE"              # cached answer: this ZIP has no penalties


def listing(get=None):
    if get is None:
        req = urllib.request.Request(LISTING, headers={"User-Agent": AGENT})
        with urllib.request.urlopen(req, timeout=120) as resp:
            return json.loads(resp.read())["data"]
    return get(LISTING)


def _cached(e):
    prefix = _cache_prefix(e)
    base = os.path.basename(prefix)
    if not os.path.isdir(CACHE):
        return None
    hits = [f for f in os.listdir(CACHE)
            if f.startswith(base) and not f.endswith(".tmp")]
    return os.path.join(CACHE, hits[0]) if hits else None


def captured(root=SNAPSHOTS):
    """The weekly captures' penalties files, as editions beside the archive's.

    CMS archives an edition only once the next one is out, so the archive
    alone runs a month behind what the weekly task already holds. A capture's
    file is byte-identical to the archived member (sha256 3683f1de... for
    August 2026), so `build` counts the pair once, and an archive dated the
    same day sorts first and keeps its CMS link. Added 2026-09-28."""
    out = []
    if not os.path.isdir(root):
        return out
    for date in sorted(os.listdir(root)):
        path = os.path.join(root, date, "penalties.csv.gz")
        if not os.path.isfile(path):
            continue
        with gzip.open(path, "rb") as fh:
            raw = fh.read()
        entry = {"date": date, "id": "capture", "name": "Weekly capture (%s)" % date,
                 "link": REPO_FILES + "snapshots/cms/%s/penalties.csv.gz" % date}
        out.append((entry, "penalties.csv.gz", raw, "capture"))
    return out


def fetch_all(offline=False, fetch=http_range, log=print):
    """[(archive entry, member name or None, raw bytes or None, note)]."""
    saved = os.path.join(CACHE, "listing.json")
    if offline:
        with open(saved, encoding="utf-8") as fh:
            raw_listing = json.load(fh)
    else:
        raw_listing = listing()
        os.makedirs(CACHE, exist_ok=True)
        with open(saved, "w", encoding="utf-8") as fh:
            json.dump(raw_listing, fh)
    entries = sorted(raw_listing, key=lambda e: (e["date"], e["url"]))
    out = []
    for e in entries:
        url = BASE + e["url"]
        hit = _cached(e)
        # A cached member is used without asking CMS again, online or not:
        # an archived edition does not change, and the weekly task should
        # cost CMS one listing request, not ninety-seven directory reads.
        if offline or hit is not None:
            if hit is None:
                out.append((e, None, None, "FAILED: not cached"))
            elif hit.endswith("__" + NONE_MARKER):
                out.append((e, None, None, "no penalties member"))
            else:
                member = os.path.basename(hit).split("__", 2)[2]
                with open(hit, "rb") as fh:
                    out.append((e, member, fh.read(), "cache"))
            continue
        try:
            m = penalty_member(zip_directory(url, fetch))
            os.makedirs(CACHE, exist_ok=True)
            if m is None:
                open(_cache_prefix(e) + NONE_MARKER, "w").close()
                out.append((e, None, None, "no penalties member"))
                continue
            path = _cache_prefix(e) + m["name"].rsplit("/", 1)[-1]
            if os.path.isfile(path) and os.path.getsize(path) == m["usize"]:
                with open(path, "rb") as fh:
                    raw, note = fh.read(), "cache"
            else:
                raw, note = zip_member(url, m, fetch), "fetched"
                with open(path + ".tmp", "wb") as fh:
                    fh.write(raw)
                os.replace(path + ".tmp", path)
            out.append((e, m["name"], raw, note))
            log("  %s  %-9s %s" % (e["date"], note, m["name"].rsplit("/", 1)[-1]))
        except Exception as exc:                                   # noqa: BLE001
            # A failed archive is named, never skipped silently: a gap in
            # the middle of a history reads as a penalty that came and went.
            out.append((e, None, None, "FAILED: %s: %s" % (type(exc).__name__, exc)))
            log("  %s  FAILED   %s" % (e["date"], exc))
    return out


def build(fetched):
    manifest, editions, seen = [], [], {}
    for e, member, raw, note in fetched:
        row = {"archive_date": e["date"], "archive": e.get("link") or BASE + e["url"],
               "archive_name": e["name"], "member": member, "note": note}
        if raw is None:
            manifest.append(row)
            continue
        digest = hashlib.sha256(raw).hexdigest()
        row.update(sha256=digest, bytes=len(raw))
        if digest in seen:
            row["duplicate_of"] = seen[digest]
            manifest.append(row)
            continue
        seen[digest] = e["date"]
        era, encoding, rows = normalise(raw)
        dates = sorted(r["date"] for r in rows if r["date"])
        processing = sorted({r["processing_date"] for r in rows if r["processing_date"]})
        row.update(era=era, encoding=encoding, rows=len(rows),
                   window=[dates[0], dates[-1]] if dates else None,
                   processing_date=processing[-1] if processing else None,
                   fines=sum(r["kind"] == "Fine" for r in rows),
                   denials=sum(r["kind"] == "Payment Denial" for r in rows))
        manifest.append(row)
        editions.append((e["date"], rows))
    failed = [m for m in manifest if m["note"].startswith("FAILED")]
    return manifest, editions, failed


def run(offline=False, log=print, captures=True):
    fetched = fetch_all(offline=offline, log=log)
    if captures:
        # Archive before capture on the same date, so the pair's surviving
        # link is CMS's own.
        fetched = sorted(fetched + captured(),
                         key=lambda t: (t[0]["date"], t[0].get("id") == "capture"))
    manifest, editions, failed = build(fetched)
    if failed:
        # Refuse to write a history with a hole in it; the hole would read
        # as every penalty in that month having vanished and returned.
        raise SystemExit("%d archive(s) failed; nothing written. Re-run: %s"
                         % (len(failed), [m["archive_date"] for m in failed]))
    history = build_history(editions)
    penalties, linked, ambiguous = link_restatements(history)
    n = write_history(penalties, os.path.join(OUT, "penalties_history.csv.gz"))
    summary = {
        "built": time.strftime("%Y-%m-%d"),
        "listing": LISTING,
        "editions": len(editions),
        "first_edition": editions[0][0], "last_edition": editions[-1][0],
        "penalties": n,
        "not_in_latest": sum(not r["in_latest"] for r in penalties),
        "with_gaps": sum(r["gaps"] > 0 for r in penalties),
        "revised": sum(r["revisions"] > 0 for r in penalties),
        "revisions_linked": linked,
        "ambiguous_versions": ambiguous,
    }
    with open(os.path.join(OUT, "manifest.json"), "w", encoding="utf-8") as fh:
        json.dump({"summary": summary, "archives": manifest}, fh,
                  indent=1, sort_keys=True)
        fh.write("\n")
    return summary, manifest


def report(summary, manifest):
    eds = [m for m in manifest if m.get("rows") is not None]
    lines = ["CMS PENALTY HISTORY, rebuilt from CMS's archive", "",
             "  %d editions, %s to %s" % (summary["editions"],
                                          summary["first_edition"],
                                          summary["last_edition"]),
             "  %d archives skipped as byte-identical re-archives" %
             sum(1 for m in manifest if m.get("duplicate_of")),
             "  %d archives with no penalties file" %
             sum(1 for m in manifest if m["note"] == "no penalties member"),
             "", "  %d distinct penalties" % summary["penalties"],
             "  %d no longer in CMS's latest file (kept here)" %
             summary["not_in_latest"],
             "  %d left CMS's file and came back at least once" %
             summary["with_gaps"],
             "  %d revised while listed (%d revisions linked); %d versions "
             "left unlinked as ambiguous" % (summary["revised"],
                                            summary["revisions_linked"],
                                            summary["ambiguous_versions"]),
             "", "  edition      era      rows  fines  denials  window"]
    for m in eds:
        lines.append("  %s  %-7s %6d %6d %8d  %s .. %s" % (
            m["archive_date"], m["era"], m["rows"], m["fines"], m["denials"],
            m["window"][0], m["window"][1]))
    return "\n".join(lines)


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--offline", action="store_true")
    ap.add_argument("--report", action="store_true")
    args = ap.parse_args(argv[1:])
    if args.report:
        with open(os.path.join(OUT, "manifest.json"), encoding="utf-8") as fh:
            doc = json.load(fh)
        print(report(doc["summary"], doc["archives"]))
        return 0
    summary, manifest = run(offline=args.offline)
    print(report(summary, manifest))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
