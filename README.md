# Nursing Home Penalty Ledger

Every penalty the Centers for Medicare & Medicaid Services (CMS) has published against a US nursing home, counted against the homes it inspected, and kept after CMS stops publishing it.

CMS publishes nursing-home penalties as a rolling three-year window. A fine older than that disappears from the current file. This project rebuilds every penalty CMS published from 2019-01 onward out of CMS's own archive of past editions, and captures each new edition weekly, so a penalty that ages out stays visible here.

**Correction, 2026-09-19: older history *can* be recovered.** This README used to say it could not. CMS keeps 97 monthly snapshot ZIPs of the whole nursing-home dataset, listed as JSON at `data.cms.gov/provider-data/api/1/archive/aggregate/theme/nursing-homes/relative`. They run from 2019-01-17 to 2026-08-26, and the oldest one contains `Penalties_Download.csv` and `HealthDeficiencies_Download.csv`. So penalties back to about 2016, and the citations this page says are not archived, are both obtainable now. What makes this worth building is not that it alone keeps the history. It's that no consumer tool shows it: ProPublica's Nursing Home Inspect, LTCCC's NursingHome411, The Care Ratings and Care Compare all stop at about three years. **Backfilled 2026-09-19.** `tools/backfill_cms.py` in the workstation read 83 distinct editions and rebuilt 83,164 penalties. 67,468 of them CMS no longer publishes, and each facility's page now lists those under "Penalties CMS no longer publishes", with the edition that last listed them. Penalties whose amount CMS revised while listing them (29,545, often cut by 35%) are shown once, at the current amount.

## Usage

`docs/` is a static site with no server, no account and no requests to third parties. Searches run in the browser.

```bash
python -m http.server 8765 --directory docs
```

Then open `http://127.0.0.1:8765/`. The page supports these links:

| Fragment | Shows |
| --- | --- |
| `#ccn=015019` | One facility: fines, payment denials, dropped penalties, inspection counts |
| `#state=IL` | A state's totals with its denominator, and its largest fine totals |
| `#q=merry wood` | A name search (a CCN works too) |

## Facility pages

```bash
python tools/facility_pages.py              # the 30 homes with the most in dropped fines
python tools/facility_pages.py --ccn 105407 # or named homes
```

One static page per home under `docs/facilities/`, listing every penalty in
CMS's archive since 2019, including those the current file no longer shows.
Each row gives the amount as first and as last published (CMS revises
amounts while a penalty is listed: Siesta Key's $799,880 fine was first
published as $125,970), the name the home used at the time, the first and
last archived editions that listed it, each linked to CMS's own ZIP, and the
edition it was gone from. A penalty that left before the usual three years
is flagged, with the file's silence on why stated rather than filled in.

Built 2026-09-27 as a demand test: whether people, and the lawyers who sue
nursing homes, find and use a complete history. A free site already shows
some old penalties; compared on Siesta Key it missed the two largest fines
and showed the $799,880 one at its first amount. See `IDEAS.md` Round 10 in
the workstation for the incumbent check.

## Where the data comes from

`docs/data/` is generated, never edited by hand. The archive and the code that reads it are in this repository, in `snapshots/` and `tools/` — see [`tools/README.md`](tools/README.md) for what each module does and the traps in CMS's own files that it handles. *(This sentence said they "live in the private `ai-workstation` repository" until 2026-09-20, which the paragraph directly below it had already contradicted since the 19th. A correction added beneath a claim does not correct the claim.)*

| Step | Command (from this repository's root) |
| --- | --- |
| Capture a CMS edition | `python tools/snapshot_cms.py` |
| Rebuild history since 2019-01 from CMS's own archive | `python tools/backfill_cms.py` |
| Rebuild this page's data | `python tools/cms_export.py --out docs/data` |
| Every figure a written piece quotes | `python tools/findings.py` |
| Both, unattended | Windows task *CMS nursing-home capture*, weekly, runs `tools/cms_scheduled.py` |

**These commands moved into this repository on 2026-09-19, and until then they
were not here.** `cms_ledger.py`, `backfill_cms.py`, `cms_export.py`,
`snapshot_cms.py` and `cms_scheduled.py` lived in a private workstation
repository, with the 69 engine tests covering them, while this repository held only
the page and its exported data. So this table named commands a reader could
not run, and the project's central claim — that the history survives here
after CMS drops it — rested on code nobody reading this could see. The archive
itself (`snapshots/`) came with them.

Python 3, **standard library only** for the engine. Only the 8 page tests need
anything installed.

The exporter computes everything through `tools/cms_ledger.py`, the command-line reader, so the page can't disagree with it. The page doesn't word its own refusals either; it prints the reader's sentences from `meta.json`.

## What it refuses to say

| Claim it won't make | Why |
| --- | --- |
| A facility with no penalty is clean | No penalty in the window means CMS published none, and that is not a finding of good care. The page says this in full every time instead of printing a zero |
| One penalty total per facility | Fines are dollars and payment denials are days. They're shown separately and never added together |
| "Nothing was dropped" before 2016 | The earliest archived edition (2019-01) reaches back to January 2016. A facility with no dropped penalty has none in that range; older history is outside every edition, not absent |
| Which rule a facility broke | The citation files are 158 MB and 66 MB a month and are not archived. The page shows deficiency counts only |
| An unknown CCN has a clean record | A number missing from CMS's files is a different fact from a facility with no penalties |

## Tests

```bash
python -m unittest discover -s tests     # 88 tests
```

**88 tests: 69 over the engine, 4 over the article's figures, 8 driving the page, 7 over the unattended run.**
All 84 are in this repository as of 2026-09-19; 76 of them used to be in the
workstation, which meant a clone could run 8.

`discover`, not a loop over `tests/test_*.py` — a file that calls
`unittest.main()` above its last class runs green while skipping it and prints
a total that looks right, which hid three tests in a sibling project here.

The 8 page tests drive the rendered page in headless Chromium and check it
against `docs/data/meta.json`, so they stay valid as the captures move the
numbers. They need Playwright (`pip install playwright`, then
`python -m playwright install chromium`) and **skip without it** — which is a
real hazard rather than a convenience, because a green `OK (skipped=8)` looks
exactly like a suite that tested the page. CI installs chromium on purpose and
fails the run if anything reports as skipped. The 69 engine tests need nothing.

## Status

**The page is live at `cms-penalty-ledger.pages.dev`**, deployed to
Cloudflare Pages on 2026-09-19. This repository is self-contained: everything
needed to capture an edition, rebuild the history from `data.cms.gov` and
regenerate the page is here, and its own suite runs against it. The figures a
written piece quotes are printed by `tools/findings.py`.

**Checked against existing tools on 2026-09-19, and re-checked on the same
day.** Every consumer tool found — ProPublica's Nursing Home Inspect,
LTCCC/NursingHome411, The Care Ratings, Care Compare, and an Apify CMS penalty
scraper — shows about three years. LTCCC's own alert page says "the past three
years" in as many words. Multi-year penalty history is a real gap, and the
correction at the top is what makes it fillable.

**The name was searched.** No product was found using "Nursing Home Penalty
Ledger", and a trademark search returns only unrelated marks (Legal Ledger, the
Ledger hardware wallet). Renamed from Docket on 2026-09-19: no nursing-home
tool used that name either, but legal software crowds it — Docket Alarm,
Clarivate Docket, Docket for in-house counsel — so a search for it would never
have found this page.

Not tagged for production. Making this repository public would run through
`PRE-PUBLIC-CHECKLIST.md` in the workstation repository first.
