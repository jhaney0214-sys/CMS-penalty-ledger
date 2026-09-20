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

## Where the data comes from

`docs/data/` is generated, never edited by hand. The archive, and the code that reads it, live in the private `ai-workstation` repository:

| Step | Command (from this repository's root) |
| --- | --- |
| Capture a CMS edition | `python tools/snapshot_cms.py` |
| Rebuild history since 2019-01 from CMS's own archive | `python tools/backfill_cms.py` |
| Rebuild this page's data | `python tools/cms_export.py --out docs/data` |
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
python -m unittest discover -s tests     # 84 tests
```

**84 tests: 69 over the engine, 8 driving the page, 7 over the unattended run.**
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

**The page is live at `cms-penalty-ledger.pages.dev`; this repository stays
private.** Deployed to Cloudflare Pages on 2026-09-19 from an account that is
not the real-name one, the same arrangement Outcrop uses. So the
surface is public and the code is not.

**That is worth stating precisely, because it bounds what the engine move
bought.** Moving the engine in makes this repository self-contained: everything
needed to capture an edition, rebuild the history from `data.cms.gov` and
regenerate the page is here, and its own suite runs against it. What it does
**not** yet do is let anyone else rebuild the data, because nobody else can
clone a private repository. The fourth production criterion — documented well
enough for someone else to run it — is met by the repository and blocked by its
visibility, which is a different thing from the defect that used to be here.

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
