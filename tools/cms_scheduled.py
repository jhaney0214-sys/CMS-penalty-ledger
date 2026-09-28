"""The unattended half of the CMS archive: capture, then commit and push it.

    python tools/cms_scheduled.py

Run weekly by the Windows task "CMS nursing-home capture". Weekly rather than
monthly because a run can land on a day the machine is off or offline, and
CMS publishes once a month: four chances per edition instead of one.
`snapshot_cms.run` already writes nothing when nothing changed, so most runs
are a no-op.

A capture that exists only on this disk is one disk failure from the same loss
the archive exists to prevent, so a new capture is committed and pushed.
Only its own directory is committed - anything else staged or modified in the
repository is left exactly as it was - and a push that fails leaves the commit
local and says so rather than retrying into a conflict.

Then the public page follows: `cms_export` rewrites `docs/data` from the
archive, and that directory alone is committed and pushed, by the same rules.
Two commits against one repository rather than one against each of two, since
the engine moved in beside the page on 2026-09-19.

Every run appends one block to snapshots/cms/.last_run.log (gitignored), which
is the only place an unattended failure is visible.
"""

import datetime
import json
import os
import subprocess
import sys
import urllib.request

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(HERE, "tools"))

import backfill_cms  # noqa: E402
import cms_export  # noqa: E402
import facility_pages  # noqa: E402
import snapshot_cms  # noqa: E402

LOG = os.path.join(HERE, "snapshots", "cms", ".last_run.log")
# The archive and the page are now the same repository. This used to point at
# a sibling directory, because the engine lived in the workstation and only
# the page lived here - which is exactly what stopped a clone rebuilding its
# own data. One repository, so one root.
LEDGER = HERE
#: What the public page is serving. Since 2026-09-27 Cloudflare Pages builds
#: the page from this repository on every push, so the page should follow the
#: push this task makes; the check stays, because "it should" is not "it did".
LIVE_META = "https://penalty-ledger.pages.dev/data/meta.json"
#: Exit code when the page is behind the repository: not a failed capture,
#: but a deploy nobody has done, and it must show in Task Scheduler.
BEHIND = 3


def _git_in(cwd, *args):
    proc = subprocess.run(["git", *args], cwd=cwd, capture_output=True,
                          text=True)
    return proc.returncode, (proc.stdout + proc.stderr).strip()


def git(*args):
    return _git_in(HERE, *args)


def _commit(git, rel, message, what):
    """Commit `rel` (a path, or a list of them) alone on main and push.
    Returns log lines; never raises."""
    paths = [rel] if isinstance(rel, str) else list(rel)
    rel = " ".join(paths)
    code, branch = git("rev-parse", "--abbrev-ref", "HEAD")
    if code or branch != "main":
        return ["  %s NOT COMMITTED: repository is on %r, not main"
                % (what, branch)]
    code, out = git("add", "--", *paths)
    if code:
        return ["  %s NOT COMMITTED: git add failed: %s" % (what, out)]
    # A pathspec on commit takes only that path, whatever else is staged.
    code, out = git("commit", "-m", message, "--", *paths)
    if code:
        return ["  %s NOT COMMITTED: %s" % (what, out)]
    code, out = git("push", "-q", "origin", "main")
    if code:
        return ["  committed %s, PUSH FAILED - it is on this disk only" % rel,
                "  %s" % out]
    return ["  committed and pushed %s" % rel]


def commit_and_push(wrote, git=git):
    """Returns a list of log lines. Never raises."""
    rel = os.path.relpath(wrote, HERE).replace(os.sep, "/")
    return _commit(git, rel, "CMS capture %s" % os.path.basename(wrote),
                   "capture")


def republish(ledger=LEDGER, export=None, git=None):
    """Re-export the page's data into the ledger repository, then commit and
    push only docs/data there. Without this the page freezes at whichever
    capture it was last built from while the archive moves on.
    Returns log lines. Never raises."""
    if not os.path.isdir(os.path.join(ledger, ".git")):
        return ["  page not refreshed: no ledger repository at %s" % ledger]
    export = export or cms_export.export
    git = git or (lambda *a: _git_in(ledger, *a))
    try:
        meta = export(os.path.join(ledger, "docs", "data"))
    except Exception as error:                                   # noqa: BLE001
        return ["  page NOT refreshed: export failed: %s: %s"
                % (type(error).__name__, error)]
    return _commit(git, "docs/data", "Data: capture %s" % meta["capture"],
                   "page data")


#: What a history refresh commits: the rebuilt history and the pages built on it.
HISTORY_PATHS = ("snapshots/cms-archive", "docs/facilities", "docs/sitemap.xml",
                 "docs/robots.txt")


def refresh_history(cache=None, backfill=None, pages=None, git=None):
    """After a new capture, fold it into the penalty history and rebuild the
    facility pages, then commit both. Added 2026-09-28: until then the pages
    moved only when somebody ran backfill_cms.py by hand.

    Only against an archive cache that already exists. Filling an empty one
    fetches about 350 MB from CMS, which is a decision for a person, not for
    a task that fires while nobody watches; with the cache in place a run
    fetches the listing and any newly archived member, a few MB a month.
    Returns log lines. Never raises."""
    cache = cache or backfill_cms.CACHE
    if not os.path.isfile(os.path.join(cache, "listing.json")):
        return ["  history NOT refreshed: no archive cache at %s;"
                " run tools/backfill_cms.py once by hand" % cache]
    backfill = backfill or (lambda: backfill_cms.run(log=lambda *a: None)[0])
    pages = pages or (lambda: facility_pages.build(every=True))
    git = git or globals()["git"]
    try:
        summary = backfill()
        entries = pages()
    except (Exception, SystemExit) as error:                     # noqa: BLE001
        return ["  history NOT refreshed: %s: %s" % (type(error).__name__, error)]
    return _commit(git, list(HISTORY_PATHS),
                   "History and %d facility pages through edition %s"
                   % (len(entries), summary["last_edition"]), "history")


def live_check(ledger=LEDGER, fetch=None):
    """(behind, log line): is the public page serving the repository's capture?

    Found 2026-09-27 checking the Ledger against the production bar: this task
    pushes new data every month CMS publishes, and the page, uploaded by hand,
    would have stayed on the old one with every run reporting success."""
    # Cloudflare answers Python's default user agent with 403.
    fetch = fetch or (lambda url: urllib.request.urlopen(urllib.request.Request(
        url, headers={"User-Agent": "cms-penalty-ledger weekly check"}), timeout=30).read().decode("utf-8"))
    with open(os.path.join(ledger, "docs", "data", "meta.json"), encoding="utf-8") as handle:
        here = json.load(handle)["capture"]
    try:
        live = json.loads(fetch(LIVE_META))["capture"]
    except Exception as error:                                   # noqa: BLE001
        return True, "  live page NOT CHECKED: %s: %s" % (type(error).__name__, error)
    if live != here:
        return True, ("  LIVE PAGE BEHIND: it serves capture %s, the repository has %s;"
                      " check the Cloudflare Pages build (notes/hosting-the-page.md)" % (live, here))
    return False, "  live page current: capture %s" % here


def main():
    lines = ["=== %s" % datetime.datetime.now().isoformat(timespec="seconds")]
    try:
        rows, wrote = snapshot_cms.run()
        lines.append(snapshot_cms.report(rows, wrote))
        if wrote:
            lines += commit_and_push(wrote)
            lines += republish()
            lines += refresh_history()
        failed = any(r["status"] in ("fetch_failed", "unresolved")
                     for r in rows)
        code = 2 if failed else 0
        behind, line = live_check()
        lines.append(line)
        if behind and code == 0:
            code = BEHIND
    except Exception as error:                                   # noqa: BLE001
        lines.append("  RUN FAILED: %s: %s" % (type(error).__name__, error))
        code = 1

    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n\n")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
