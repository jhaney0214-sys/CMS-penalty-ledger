#!/usr/bin/env python3
"""Every nursing home's star ratings, edition by edition, from CMS's archive.

    python tools/ratings_history.py            # fetch what is missing, rebuild
    python tools/ratings_history.py --offline  # rebuild from the local cache only

Writes snapshots/cms-ratings/ratings_history.csv.gz (one row per run of
unchanged ratings per home) and snapshots/cms-ratings/manifest.json (every
archive read, and for each edition the share of homes whose overall rating
moved).

Written 2026-09-28. Care Compare shows a home's rating today and nothing
before it; this is the rest. It reads the ProviderInfo member of the same
monthly ZIPs `backfill_cms.py` reads, by the same HTTP range requests, and
caches only the six columns it keeps, not the 10 MB member.

## What a rating change is, and is not

A star moves when the home changes and also when CMS changes the formula or
refreshes a component on its quarterly cycle. The file cannot tell these
apart. So every edition carries `moved`, the share of all rated homes whose
overall rating changed in it: a home's drop in an edition where a quarter of
the country moved is a different fact from a drop in one where 3% did. No
output here says why a rating changed.

## Three header eras, each seen in the real archive

2019: PROVNUM, OVERALL_RATING, SURVEY_RATING, QUALITY_RATING,
STAFFING_RATING, FILEDATE. 2020-08 on: Federal Provider Number, Overall
Rating, Health Inspection Rating, QM Rating, Staffing Rating, Processing
Date. Current: the CCN column renamed to CMS Certification Number (CCN).
Between 2019-04 and 2019-12 the 2019 names come in mixed case (Overall_Rating,
Quality_Rating), so names match without regard to case. An unrecognised
header is an error, never a guess, and it is found from the first 64 KB of
the member, before the rest is fetched.
"""

import argparse
import collections
import csv
import gzip
import hashlib
import io
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(HERE, "tools"))
import backfill_cms as bf                                       # noqa: E402

OUT = os.path.join(HERE, "snapshots", "cms-ratings")
CACHE = os.path.join(HERE, "data", "cms-ratings")
HISTORY = os.path.join(OUT, "ratings_history.csv.gz")

FIELDS = ("ccn", "overall", "inspection", "quality", "staffing")
ALIASES = {
    "ccn": ("PROVNUM", "Federal Provider Number", "CMS Certification Number (CCN)"),
    "overall": ("OVERALL_RATING", "Overall Rating"),
    "inspection": ("SURVEY_RATING", "Health Inspection Rating"),
    "quality": ("QUALITY_RATING", "QM Rating"),
    "staffing": ("STAFFING_RATING", "Staffing Rating"),
    "processed": ("FILEDATE", "Processing Date"),
}
NONE_MARKER = "NONE"
HISTORY_COLUMNS = ("ccn", "from_edition", "to_edition", "editions", "overall",
                   "inspection", "quality", "staffing", "in_latest")


def provider_member(members):
    """The ProviderInfo file, ignoring macOS resource forks. None if absent."""
    hits = [m for m in members
            if "providerinfo" in m["name"].rsplit("/", 1)[-1].lower().replace("_", "")
            and "__MACOSX" not in m["name"]
            and not m["name"].rsplit("/", 1)[-1].startswith("._")]
    if len(hits) > 1:
        raise ValueError("more than one ProviderInfo member: %s" % [m["name"] for m in hits])
    return hits[0] if hits else None


def columns(header):
    """{field: index} for the kept fields; an unknown header is refused."""
    out = {}
    for field, names in ALIASES.items():
        wanted = {n.lower() for n in names}
        found = [i for i, h in enumerate(header) if h.strip().lower() in wanted]
        if len(found) != 1:
            raise ValueError("ProviderInfo header: no single column for %s in %s"
                             % (field, header[:12]))
        out[field] = found[0]
    return out


def _star(value):
    value = value.strip()
    if value in ("", "NA", "N/A", "."):
        return ""
    number = float(value)
    if number != int(number) or not 1 <= number <= 5:
        raise ValueError("not a star rating: %r" % value)
    return str(int(number))


def peek_header(url, m, fetch=bf.http_range, size=65536):
    """The member's header row, from its first `size` compressed bytes."""
    head, _ = fetch(url, m["offset"], m["offset"] + 29)
    nlen, elen = bf.struct.unpack("<HH", head[26:30])
    start = m["offset"] + 30 + nlen + elen
    data, _ = fetch(url, start, start + min(size, m["csize"]) - 1)
    text = data if m["method"] == 0 else bf.zlib.decompressobj(-15).decompress(data)
    line = bf.decode(text.split(b"\n", 1)[0])[0]
    return next(csv.reader(io.StringIO(line)))


def reduce(raw):
    """Raw member bytes -> (processing date, [(ccn, overall, inspection,
    quality, staffing)]). CCNs are zero-padded to six, as the penalties are."""
    text, _ = bf.decode(raw)
    reader = csv.reader(io.StringIO(text))
    idx = columns(next(reader))
    rows, processed = [], set()
    for rec in reader:
        if not rec or not rec[idx["ccn"]].strip():
            continue
        ccn = rec[idx["ccn"]].strip().upper().zfill(6)
        rows.append((ccn,) + tuple(_star(rec[idx[f]]) for f in FIELDS[1:]))
        processed.add(rec[idx["processed"]].strip()[:10])
    return (max(processed) if processed else ""), rows


# ------------------------------------------------------------ the cache

def _cache_path(entry):
    return os.path.join(CACHE, "%s__%s.csv.gz" % (entry["date"], entry["id"]))


def _write_cache(path, processed, sha, member, rows):
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["#", processed, sha, member])
    w.writerow(FIELDS)
    w.writerows(rows)
    tmp = path + ".tmp"
    with open(tmp, "wb") as fh, gzip.GzipFile(fileobj=fh, mode="wb", mtime=0) as gz:
        gz.write(buf.getvalue().encode("utf-8"))
    os.replace(tmp, path)


def _read_cache(path):
    with gzip.open(path, "rt", encoding="utf-8", newline="") as fh:
        reader = csv.reader(fh)
        _, processed, sha, member = next(reader)
        next(reader)
        return processed, sha, member, [tuple(r) for r in reader]


def fetch_all(offline=False, fetch=bf.http_range, log=print, entries=None):
    """[(archive entry, note, (processed, sha, member, rows) or None)]."""
    if entries is None:
        with open(os.path.join(bf.CACHE, "listing.json"), encoding="utf-8") as fh:
            entries = json.load(fh)
    entries = sorted(entries, key=lambda e: (e["date"], e["url"]))
    os.makedirs(CACHE, exist_ok=True)
    out = []
    for e in entries:
        path = _cache_path(e)
        none = path[:-len(".csv.gz")] + "__" + NONE_MARKER
        if os.path.isfile(path):
            out.append((e, "cache", _read_cache(path)))
            continue
        if os.path.isfile(none):
            out.append((e, "no ProviderInfo member", None))
            continue
        if offline:
            out.append((e, "FAILED: not cached", None))
            continue
        url = bf.BASE + e["url"]
        try:
            m = provider_member(bf.zip_directory(url, fetch))
            if m is None:
                open(none, "w").close()
                out.append((e, "no ProviderInfo member", None))
                continue
            columns(peek_header(url, m, fetch))
            raw = bf.zip_member(url, m, fetch)
            processed, rows = reduce(raw)
            sha = hashlib.sha256(raw).hexdigest()
            _write_cache(path, processed, sha, m["name"], rows)
            out.append((e, "fetched", (processed, sha, m["name"], rows)))
            log("  %s  fetched  %d homes" % (e["date"], len(rows)))
            time.sleep(bf.PAUSE)
        except Exception as exc:                                  # noqa: BLE001
            out.append((e, "FAILED: %s: %s" % (type(exc).__name__, exc), None))
            log("  %s  FAILED   %s" % (e["date"], exc))
    return out


# ------------------------------------------------------------ the history

def build(fetched):
    """-> (manifest rows, [(edition date, {ccn: ratings})]); byte-identical
    re-archives are counted once."""
    manifest, editions, seen = [], [], {}
    for e, note, got in fetched:
        row = {"archive_date": e["date"], "archive": bf.BASE + e["url"], "note": note}
        if got is None:
            manifest.append(row)
            continue
        processed, sha, member, rows = got
        row.update(sha256=sha, member=member, processed=processed, homes=len(rows))
        if sha in seen:
            row["duplicate_of"] = seen[sha]
            manifest.append(row)
            continue
        seen[sha] = e["date"]
        manifest.append(row)
        editions.append((e["date"], {r[0]: r[1:] for r in rows}))
    return manifest, editions


def moved(editions):
    """{edition: (homes rated in both it and the one before, of those how many
    changed overall rating)}."""
    out = {}
    for (_, before), (date, now) in zip(editions, editions[1:]):
        both = [c for c in now if c in before and now[c][0] and before[c][0]]
        out[date] = (len(both), sum(now[c][0] != before[c][0] for c in both))
    return out


def runs(editions):
    """One record per home per stretch of identical ratings. A home absent
    from an edition ends its run; coming back starts a new one."""
    open_runs, done = {}, []
    for date, homes in editions:
        for ccn in list(open_runs):
            if ccn not in homes:
                done.append(open_runs.pop(ccn))
        for ccn, stars in homes.items():
            run = open_runs.get(ccn)
            if run is not None and run["stars"] == stars:
                run["to"], run["n"] = date, run["n"] + 1
                continue
            if run is not None:
                done.append(run)
            open_runs[ccn] = {"ccn": ccn, "from": date, "to": date, "n": 1, "stars": stars}
    for run in done:
        run["in_latest"] = False
    for run in open_runs.values():
        run["in_latest"] = True
    done += open_runs.values()
    return sorted(done, key=lambda r: (r["ccn"], r["from"]))


def write_history(done, path=HISTORY):
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(HISTORY_COLUMNS)
    for r in done:
        w.writerow([r["ccn"], r["from"], r["to"], r["n"]] + list(r["stars"])
                   + ["yes" if r["in_latest"] else "no"])
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "wb") as fh, gzip.GzipFile(fileobj=fh, mode="wb", mtime=0) as gz:
        gz.write(buf.getvalue().encode("utf-8"))
    os.replace(tmp, path)


def read_history(path=HISTORY):
    """{ccn: [run rows, oldest first]}."""
    out = collections.defaultdict(list)
    with gzip.open(path, "rt", encoding="utf-8", newline="") as fh:
        for r in csv.DictReader(fh):
            out[r["ccn"]].append(r)
    return out


def read_moved(path=os.path.join(OUT, "manifest.json")):
    """{edition: share of rated homes whose overall rating changed in it}."""
    with open(path, encoding="utf-8") as fh:
        doc = json.load(fh)
    return {d: (m["changed"] / m["compared"] if m["compared"] else None)
            for d, m in doc["moved"].items()}


def run(offline=False, log=print):
    fetched = fetch_all(offline=offline, log=log)
    manifest, editions = build(fetched)
    failed = [m for m in manifest if m["note"].startswith("FAILED")]
    if failed:
        # A hole would read as every home's rating stopping and restarting.
        raise SystemExit("%d archive(s) failed; nothing written. Re-run: %s"
                         % (len(failed), [m["archive_date"] for m in failed]))
    done = runs(editions)
    write_history(done)
    shifts = moved(editions)
    summary = {"built": time.strftime("%Y-%m-%d"), "editions": len(editions),
               "first_edition": editions[0][0], "last_edition": editions[-1][0],
               "homes": len({r["ccn"] for r in done}), "runs": len(done)}
    with open(os.path.join(OUT, "manifest.json"), "w", encoding="utf-8", newline="\n") as fh:
        json.dump({"summary": summary, "archives": manifest,
                   "moved": {d: {"compared": n, "changed": c} for d, (n, c) in shifts.items()}},
                  fh, indent=1, sort_keys=True)
        fh.write("\n")
    return summary, shifts


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--offline", action="store_true")
    args = ap.parse_args(argv)
    summary, shifts = run(offline=args.offline)
    print("%(editions)d editions, %(first_edition)s to %(last_edition)s; "
          "%(homes)d homes, %(runs)d runs of unchanged ratings" % summary)
    for d, (n, c) in sorted(shifts.items()):
        print("  %s  %5.1f%% of %d rated homes changed overall rating" % (d, 100.0 * c / n if n else 0, n))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
