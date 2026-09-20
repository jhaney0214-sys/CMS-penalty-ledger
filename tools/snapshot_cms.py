#!/usr/bin/env python3
"""Archive CMS's nursing-home files before the rolling window drops them.

    python tools/snapshot_cms.py            # archive if anything changed
    python tools/snapshot_cms.py --dry-run  # resolve and report, write nothing
    python tools/snapshot_cms.py --status   # what is already archived

## Why this exists and why it is urgent in a way nothing else in the backlog is

CMS publishes nursing-home penalties, citations and survey summaries as **a
rolling three-year window**, refreshed monthly. *(Corrected 2026-09-19: the
paragraph below is wrong about the archive. CMS keeps 97 monthly ZIPs back to
2019-01, and `backfill_cms.py` rebuilds history from them. This capture
remains insurance against CMS pruning that archive.)* Every row carries a
`Processing Date` - the snapshot stamp - and the files simply stop containing
rows older than three years. There is no archive endpoint that a plain fetch
can read: `data.cms.gov/provider-data/archived-data/nursing-homes` is a
JavaScript application and returns no file list. **So history is obtainable
going forward and not retroactively**, and every month nobody runs this is a
month permanently missing from any trajectory the eventual product wants to
show.

Every other item in this workstation's backlog keeps its history whether it is
built today or in March. This one does not, which is the whole argument for
starting the archive before the product that will read it.

## What is archived and what is deliberately not

Measured 2026-08 on the live files:

| file | size | archived |
|---|---|---|
| Penalties | 2.6 MB | **yes** - this is the product's core |
| Citation Code Look-up | 0.1 MB | **yes** - the join target for tag codes |
| Survey Summary | 9.5 MB | **yes** |
| Health Deficiencies | **165 MB** | no - manifest only |
| Fire Safety Deficiencies | **66 MB** | no - manifest only |

Git is the wrong store for 231 MB a month. The two big files are recorded in
the manifest - name, URL, size where the server reports one, and the metastore
`modified` date - so a later reader can see exactly what existed on each date
and prove precisely what was not kept, rather than finding a gap and guessing.
**Naming the loss is the point.** A snapshot that silently covers three of five
files looks identical to one that covers all five.

A subset of the big files would probably be affordable - most of their bulk is
`Deficiency Description` and `Deficiency Category`, which join back from the
lookup table, plus provider name and address, which join from the CCN. That is
left undecided on purpose: the provider file is *itself* a rolling snapshot, so
a join that works today is not guaranteed to work against a facility CMS has
since dropped, and choosing columns is an analysis decision this archive should
not make on the product's behalf.

## Idempotence

A month with no new publication must not create a second copy. Each file is
compared by sha256 against the most recent archived copy, and the run writes a
new dated directory only when something actually differs. `--status` reports
what is held without touching the network.
"""

import argparse
import datetime
import gzip
import hashlib
import json
import os
import sys
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ARCHIVE = os.path.join(HERE, "snapshots", "cms")

CATALOG = ("https://data.cms.gov/provider-data/api/1/metastore/schemas/"
           "dataset/items/%s?show-reference-ids=true")
AGENT = "ai-workstation-cms-snapshot/1.0"
TIMEOUT = 180

# identifier -> (short name, archive the bytes?)
DATASETS = {
    "g6vv-u9sr": ("penalties", True),
    "tagd-9999": ("citation_codes", True),
    "tbry-pc2d": ("survey_summary", True),
    "r5ix-sfxw": ("health_deficiencies", False),
    "ifjz-ge4w": ("fire_safety_deficiencies", False),
}


def _get(url, timeout=TIMEOUT):
    req = urllib.request.Request(url, headers={"User-Agent": AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return response.read(), dict(response.headers)


def resolve(identifier, get=_get):
    """The dataset's current download URL and metadata.

    Resolved every run rather than hardcoded: the URL embeds a content hash and
    a build timestamp that change with every monthly publication, so a stored
    URL is a URL to last month's file at best and a 404 at worst.
    """
    raw, _ = _get_or(get, CATALOG % identifier)
    meta = json.loads(raw.decode("utf-8"))
    url = None
    for dist in meta.get("distribution", []):
        data = dist.get("data", dist)
        if data.get("downloadURL"):
            url = data["downloadURL"]
            break
    if not url:
        raise RuntimeError("no downloadURL for %s" % identifier)
    return {"identifier": identifier, "title": meta.get("title", ""),
            "modified": meta.get("modified", ""), "url": url,
            "filename": url.rsplit("/", 1)[-1]}


def _get_or(get, url):
    got = get(url)
    return got if isinstance(got, tuple) else (got, {})


def head_size(url, get=_get):
    """Content-length, or None. None means unknown, never zero."""
    try:
        req = urllib.request.Request(url, method="HEAD",
                                     headers={"User-Agent": AGENT})
        with urllib.request.urlopen(req, timeout=60) as response:
            value = response.headers.get("Content-Length")
            return int(value) if value else None
    except Exception:                                            # noqa: BLE001
        return None


def existing_digests():
    """{short name: sha256} from the most recent dated directory holding each.

    Read per file rather than per run, because a run can legitimately archive
    some files and fail on others, and the next run should only re-fetch what
    is actually missing.
    """
    found = {}
    if not os.path.isdir(ARCHIVE):
        return found
    for stamp in sorted(os.listdir(ARCHIVE), reverse=True):
        path = os.path.join(ARCHIVE, stamp, "manifest.json")
        if not os.path.isfile(path):
            continue
        with open(path, encoding="utf-8") as handle:
            manifest = json.load(handle)
        for row in manifest.get("files", []):
            if row.get("sha256") and row["name"] not in found:
                found[row["name"]] = row["sha256"]
    return found


def run(today=None, dry_run=False, get=_get, sizer=head_size):
    """Returns (rows, wrote_dir_or_None). Never raises for one bad file."""
    today = today or datetime.date.today()
    held = existing_digests()
    rows, changed = [], False

    for identifier, (name, archive_it) in sorted(
            DATASETS.items(), key=lambda kv: kv[1][0]):
        row = {"name": name, "identifier": identifier, "archived": archive_it}
        try:
            info = resolve(identifier, get=get)
        except Exception as error:                               # noqa: BLE001
            row.update(status="unresolved",
                       detail="%s: %s" % (type(error).__name__, error))
            rows.append(row)
            continue

        row.update(title=info["title"], modified=info["modified"],
                   url=info["url"], filename=info["filename"])

        if not archive_it:
            row.update(status="manifest_only", bytes=sizer(info["url"]))
            rows.append(row)
            continue

        try:
            body, _ = _get_or(get, info["url"])
        except Exception as error:                               # noqa: BLE001
            row.update(status="fetch_failed",
                       detail="%s: %s" % (type(error).__name__, error))
            rows.append(row)
            continue

        digest = hashlib.sha256(body).hexdigest()
        row.update(sha256=digest, bytes=len(body),
                   rows=max(0, body.count(b"\n") - 1))
        if held.get(name) == digest:
            row["status"] = "unchanged"
        else:
            row["status"] = "new"
            row["_body"] = body
            changed = True
        rows.append(row)

    if dry_run or not changed:
        return rows, None

    stamp = today.isoformat()
    out = os.path.join(ARCHIVE, stamp)
    os.makedirs(out, exist_ok=True)
    for row in rows:
        body = row.pop("_body", None)
        if body is None:
            continue
        with gzip.open(os.path.join(out, "%s.csv.gz" % row["name"]), "wb") as f:
            f.write(body)
    with open(os.path.join(out, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump({"captured": stamp, "files": rows}, f,
                  indent=2, sort_keys=True)
        f.write("\n")
    return rows, out


def report(rows, wrote, dry_run=False):
    lines = ["CMS NURSING-HOME SNAPSHOT - %s" % datetime.date.today(), ""]
    label = {"new": "NEW      ", "unchanged": "unchanged",
             "manifest_only": "manifest ", "fetch_failed": "FAILED   ",
             "unresolved": "FAILED   "}
    for row in rows:
        size = row.get("bytes")
        size = "%.1f MB" % (size / 1048576.0) if size else "size unknown"
        lines.append("  %s %-26s %s" % (label[row["status"]], row["name"], size))
        if row.get("detail"):
            lines.append("      %s" % row["detail"])
        elif row["status"] == "manifest_only":
            lines.append("      not archived by design - too large for git; "
                         "recorded so the gap is legible")

    failed = [r for r in rows if r["status"] in ("fetch_failed", "unresolved")]
    lines += ["", "SUMMARY"]
    if dry_run:
        lines.append("  dry run - nothing written")
    elif wrote:
        lines.append("  wrote %s" % os.path.relpath(wrote, HERE))
    else:
        lines.append("  nothing new to archive; no directory written")
    if failed:
        lines += ["",
                  "  %d file(s) COULD NOT BE FETCHED. That is not 'unchanged'."
                  % len(failed),
                  "  Re-run before the next monthly publication, or that "
                  "month is gone."]
    return "\n".join(lines)


def status():
    lines = ["CMS SNAPSHOT ARCHIVE - %s" % os.path.relpath(ARCHIVE, HERE), ""]
    if not os.path.isdir(ARCHIVE):
        return "\n".join(lines + ["  nothing archived yet"])
    stamps = sorted(d for d in os.listdir(ARCHIVE)
                    if os.path.isdir(os.path.join(ARCHIVE, d)))
    for stamp in stamps:
        path = os.path.join(ARCHIVE, stamp, "manifest.json")
        kept = []
        if os.path.isfile(path):
            with open(path, encoding="utf-8") as handle:
                kept = [r["name"] for r in json.load(handle).get("files", [])
                        if r.get("status") == "new"]
        lines.append("  %s  %s" % (stamp, ", ".join(kept) or "(no files)"))
    lines += ["", "  %d capture(s)." % len(stamps),
              "  CMS keeps three years. Anything older than the earliest "
              "capture above is gone."]
    return "\n".join(lines)


def main(argv):
    ap = argparse.ArgumentParser(description="Archive CMS nursing-home files")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--status", action="store_true")
    args = ap.parse_args(argv[1:])

    if args.status:
        print(status())
        return 0

    rows, wrote = run(dry_run=args.dry_run)
    print(report(rows, wrote, dry_run=args.dry_run))
    if any(r["status"] in ("fetch_failed", "unresolved") for r in rows):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
