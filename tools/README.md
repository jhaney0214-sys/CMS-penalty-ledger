# The engine behind the Ledger

Five stdlib-only modules: `snapshot_cms.py` keeps each CMS edition,
`backfill_cms.py` rebuilds everything before the first capture from CMS's own
monthly archive, `cms_ledger.py` reads both, `cms_export.py` writes them out as
the data behind the page in `docs/`, and `cms_scheduled.py` is what the weekly
Windows task runs — capture, commit and push only the new capture, then
re-export and push the page's data.

All five, and the 76 tests over them, moved into this repository on 2026-09-19.
**This file followed them on 2026-09-20, and the gap between those two dates is
the thing worth recording.** The prose below lived in the workstation's
`tools/README.md`, describing these modules as though the code were still
there, with run commands that had silently become wrong — five of that file's
eight `PYTHONPATH=. python tests/test_*.py` lines pointed at files that were no
longer in that directory. Nobody noticed for a day, because a README is not
executed. It was found by running its own commands rather than reading them,
while a claims ledger was being added elsewhere in the workstation.

```bash
python -m unittest discover -s ../tests   # from this directory
python -m unittest discover -s tests      # from the repository root, 130 tests
```

## `snapshot_cms.py` — archive a source that discards its own history

```bash
python snapshot_cms.py --status
```

CMS publishes nursing-home penalties and citations as a rolling three-year
window. *(This said CMS "offers no readable archive". It does, and
`backfill_cms.py` below reads it. Capturing still matters as insurance
against CMS pruning that archive.)* Writes `snapshots/cms/<date>/`, skips a month where nothing
changed, and reports a file it could not fetch as **failed rather than
unchanged**. The monthly audit raises a capture older than 40 days as a
finding. See `snapshots/cms/README.md`.

## `backfill_cms.py` — rebuild the history CMS still keeps

```bash
python backfill_cms.py            # read CMS's archive, rebuild the history
python backfill_cms.py --offline  # rebuild from data/cms-archive/ only
python backfill_cms.py --report
```

CMS keeps 97 monthly ZIPs of its nursing-home files, listed as JSON at
`data.cms.gov/provider-data/api/1/archive/aggregate/theme/nursing-homes/relative`
(the archive page is a JavaScript app over this). The tool reads each ZIP's
directory with an HTTP range request and fetches only the penalties member,
about 350 MB instead of 3.6 GB. It writes `snapshots/cms-archive/`: one row
per penalty ever published since the 2019-01 edition (83,164, of which
67,468 are no longer in CMS's current file), plus a manifest of every archive
with its sha256.

Checked two ways on 2026-09-19:
- The penalties it marks as still published equal the 2026-09-18 capture,
  row for row, as a multiset (15,696).
- 60 of 60 sampled dropped penalties are present in their last edition and
  absent from the next distinct one.

Traps it handles, all found in the real files:
- four header eras;
- `Fine ID` only in the newest, so penalties are keyed on their fields, as a
  multiset;
- five byte-identical re-archives;
- nine ZIPs with no penalties file;
- `__MACOSX` junk;
- **amounts revised in place.** 29,545 penalties changed amount while
  listed, often by 35%, and unlinked each would have read as "dropped" while
  CMS still published the new figure.

## `cms_ledger.py` — read the archive `snapshot_cms.py` keeps

```bash
python cms_ledger.py --captures           # what is held
python cms_ledger.py --state IL           # a state, with its denominator
python cms_ledger.py --ccn 015019         # one facility's record
python cms_ledger.py --name "merry wood"  # find a facility by name
python cms_ledger.py --changes            # what moved between two captures
python cms_ledger.py --history 015019     # every CMS edition since 2019-01
```

Offline: a report is a function of a dated directory, so the same capture
gives the same answer in five years. Most of its 22 tests are about what it
refuses to say.

**An empty record is not a clean record.** 7,915 of the 14,690 facilities in
the first capture have no penalty row, and that means CMS published none
inside a rolling window — not that the facility is fine. A CCN that is not in
the capture at all is a third thing again, and exits non-zero rather than
printing an empty report. The workstation's `METHOD.md` has now recorded five instruments
that reported "could not look" as "found nothing"; the state report also
carries its denominator for the same reason, since 39 penalised facilities is
a different claim in a state with 224 of them than in a state with 40.

**Fines and payment denials are never added together** — 13,256 fines and
2,440 denials, one measured in dollars and the other in days. A single
"penalty total" would have to drop one silently.

**`--changes` is why the archive exists.** A row in an earlier capture and
absent from a later one has aged out of CMS's three-year window, and no later
fetch can recover it. Fines are matched on CMS's own `Fine ID`; denials carry
no id at all, so they are matched on CCN, date, start and length together —
which means a restated denial reads as one dropped and one added rather than
as an amendment, and the report says so rather than leaving it to be
discovered. With one capture held it says there is nothing to compare, which
is not the same as reporting no change.

**What it cannot do, because the archive does not hold it:** name a citation
tag. The Health and Fire Safety deficiency files are large — see below — and
are recorded by manifest only, so deficiencies are reported as counts by
category from the survey summary. Every report names the files that are not
held rather than leaving their absence to be inferred.

**An unresolved disagreement, recorded rather than silently resolved.** This
paragraph said those files are **165 MB and 66 MB**; `../README.md`'s "What it
refuses to say" table says **158 MB and 66 MB**. Both were written by hand from
a manifest nobody has re-read. One of them is wrong and it is not obvious
which, so neither is repeated as fact here. Resolving it means reading the
manifest in `snapshots/cms-archive/` and then correcting both files — and it is
exactly the kind of claim `AI Workstation/tools/claims.py` exists to pin, which
is the argument for this repository adopting a `claims.json` of its own.
