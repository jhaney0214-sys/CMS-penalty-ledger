# Docket

Every penalty the Centers for Medicare & Medicaid Services (CMS) has published against a US nursing home, counted against the homes it inspected, and kept after CMS stops publishing it.

CMS publishes nursing-home penalties as a rolling three-year window. A fine older than that disappears from the public file, and CMS's archive page offers no machine-readable way back to it. This project keeps each monthly edition from 2026-09-18 onward, so a penalty that ages out stays visible here. Anything from before the first capture can't be recovered.

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

| Step | Command (from the workstation root) |
| --- | --- |
| Capture a CMS edition | `python tools/snapshot_cms.py` |
| Rebuild this page's data | `python tools/cms_export.py --out CMS-penalty-ledger/docs/data` |
| Both, unattended | Windows task *CMS nursing-home capture*, weekly, runs `tools/cms_scheduled.py` |

The exporter computes everything through `tools/cms_ledger.py`, the command-line reader, so the page can't disagree with it. The page doesn't word its own refusals either; it prints the reader's sentences from `meta.json`.

## What it refuses to say

| Claim it won't make | Why |
| --- | --- |
| A facility with no penalty is clean | No penalty in the window means CMS published none, and that is not a finding of good care. The page says this in full every time instead of printing a zero |
| One penalty total per facility | Fines are dollars and payment denials are days. They're shown separately and never added together |
| "Nothing was dropped" | With one capture held there's nothing to compare yet, and the page says exactly that |
| Which rule a facility broke | The citation files are 158 MB and 66 MB a month and are not archived. The page shows deficiency counts only |
| An unknown CCN has a clean record | A number missing from CMS's files is a different fact from a facility with no penalties |

## Tests

```bash
python tests/test_page.py
```

Seven tests drive the rendered page in headless Chromium and check it against `docs/data/meta.json`, so they stay valid as the captures change. They need Playwright (`pip install playwright`, then `python -m playwright install chromium`) and skip cleanly without it. The exporter's own tests are in the workstation repository, at `tools/tests/test_cms_export.py`.

## Status

Private, unpublished and not tagged for production. The name "Docket" hasn't been checked against existing tools, and publishing requires going through `PRE-PUBLIC-CHECKLIST.md` in the workstation repository first.
