# CMS nursing-home snapshots

Monthly captures of CMS's nursing-home files, taken because **CMS does not
keep them**.

`data.cms.gov/provider-data` publishes penalties, citations and survey
summaries as a **rolling three-year window**, refreshed monthly. Rows older
than three years simply stop appearing, and the archive page
(`/provider-data/archived-data/nursing-homes`) is a JavaScript application
that returns no file list to a plain fetch.

**Corrected 2026-09-19: history *is* obtainable retroactively, and has been
obtained.** `tools/backfill_cms.py` rebuilt it into `snapshots/cms-archive/`. The paragraph
below originally concluded "going forward and not retroactively". That was
inferred from the plain fetch failing, and the page was never loaded in a
browser. Loaded, it calls
`data.cms.gov/provider-data/api/1/archive/aggregate/theme/nursing-homes/relative`, which lists 97 public
monthly ZIPs from 2019-01-17 to 2026-08-26 (~38 MB each). The 2019-01-17 ZIP
holds `Penalties_Download.csv`, `HealthDeficiencies_Download.csv` and
`ProviderInfo_Download.csv`. The weekly capture is still worth running as
insurance against CMS pruning that archive, but it is no longer the only
record.

*(Superseded 2026-09-19. With CMS's archive in reach, the cost below does not
grow while it waits.)* That is what makes this the one thing in the backlog whose cost grows while it
waits. Every other data source this workstation reads — Austin's permits back
to 1921, New Orleans' STR applications to 2017, the ISO queues' withdrawn
tabs — will still have its history next year.

```bash
python tools/snapshot_cms.py            # capture if anything changed
python tools/snapshot_cms.py --dry-run  # resolve and report, write nothing
python tools/snapshot_cms.py --status   # what is held

python tools/cms_ledger.py --state IL   # and this reads what was kept
python tools/cms_ledger.py --ccn 015019
python tools/cms_ledger.py --changes    # what CMS stopped publishing
```

**Since 2026-09-18 it runs unattended.** The Windows task *CMS nursing-home
capture* runs `tools/cms_scheduled.py` every Friday at 09:00 (and at next
logon if the machine was off), which captures, then commits and pushes only
the new capture directory. Weekly rather than monthly so each monthly edition
gets four chances. Every run appends to `snapshots/cms/.last_run.log`, which
is gitignored and is the only place an unattended failure shows.

Until then, the only safeguard was the monthly audit, which reports a capture
older than 40 days - but only when a session starts, and it warns rather than
captures. It is still there as the second line.

## What each capture holds

| file | size | kept |
|---|---|---|
| `penalties.csv.gz` | 2.6 MB raw | **yes** — fines and payment denials, the core |
| `survey_summary.csv.gz` | 9.5 MB raw | **yes** |
| `citation_codes.csv.gz` | 0.1 MB raw | **yes** — the join target for tag codes |
| Health Deficiencies | **158 MB** | **no** — manifest only |
| Fire Safety Deficiencies | **66 MB** | **no** — manifest only |

About 1.8 MB per capture, so roughly 22 MB a year.

**The two big files are named in every manifest and their bytes are not
kept.** Git is the wrong store for 224 MB a month. Recording the URL, size and
publication date means a later reader can see exactly what existed on each
date and prove precisely what was not kept — rather than finding a gap and
guessing. A snapshot that silently covers three of five files looks identical
to one that covers all five.

**A column subset of the big files is the obvious fix and is deliberately not
taken yet.** Most of their bulk is `Deficiency Description` and `Deficiency
Category`, which join back from the lookup table, plus provider name and
address, which join from the CCN. But the provider file is *itself* a rolling
snapshot, so a join that works today is not guaranteed against a facility CMS
has since dropped — and choosing columns is an analysis decision this archive
should not make on the product's behalf. Decide it when the product exists.

## Reading a capture

`tools/cms_ledger.py` is the reader, and `tools/README.md` says what it
refuses to claim — an empty record is not a clean record, fines and payment
denials are never summed, and one capture is not "no change". Its `--changes`
report is the only place a row CMS has dropped is visible at all. To read the
files directly instead:

```python
import csv, gzip, io
raw = gzip.open("snapshots/cms/2026-09-18/penalties.csv.gz",
                "rt", encoding="utf-8").read()
rows = list(csv.DictReader(io.StringIO(raw)))
```

The first capture, 2026-09-18, holds 15,696 penalties across 6,775 facilities:
13,256 fines totalling $456,752,787 and 2,440 payment denials, with penalty
dates from 2023-08-19 to 2026-07-29 and a `Processing Date` of 2026-08-01.

**`Processing Date` is the file's own stamp, not the capture date.** They
differ — CMS publishes on the first of the month and this archive captures
whenever it is run — so use the directory name for "when we got it" and
`Processing Date` for "what CMS called this edition".

## Where this belongs long term

Here rather than in Tailings, whose scope is environmental and safety records
near an address; nursing-home quality is a different domain and a different
reader. When the CMS ledger gets its own repository, this directory and
`tools/snapshot_cms.py` move with their history intact.
