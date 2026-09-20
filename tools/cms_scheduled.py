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
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(HERE, "tools"))

import cms_export  # noqa: E402
import snapshot_cms  # noqa: E402

LOG = os.path.join(HERE, "snapshots", "cms", ".last_run.log")
# The archive and the page are now the same repository. This used to point at
# a sibling directory, because the engine lived in the workstation and only
# the page lived here - which is exactly what stopped a clone rebuilding its
# own data. One repository, so one root.
LEDGER = HERE


def _git_in(cwd, *args):
    proc = subprocess.run(["git", *args], cwd=cwd, capture_output=True,
                          text=True)
    return proc.returncode, (proc.stdout + proc.stderr).strip()


def git(*args):
    return _git_in(HERE, *args)


def _commit(git, rel, message, what):
    """Commit `rel` alone on main and push. Returns log lines; never raises."""
    code, branch = git("rev-parse", "--abbrev-ref", "HEAD")
    if code or branch != "main":
        return ["  %s NOT COMMITTED: repository is on %r, not main"
                % (what, branch)]
    code, out = git("add", "--", rel)
    if code:
        return ["  %s NOT COMMITTED: git add failed: %s" % (what, out)]
    # A pathspec on commit takes only that path, whatever else is staged.
    code, out = git("commit", "-m", message, "--", rel)
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


def main():
    lines = ["=== %s" % datetime.datetime.now().isoformat(timespec="seconds")]
    try:
        rows, wrote = snapshot_cms.run()
        lines.append(snapshot_cms.report(rows, wrote))
        if wrote:
            lines += commit_and_push(wrote)
            lines += republish()
        failed = any(r["status"] in ("fetch_failed", "unresolved")
                     for r in rows)
        code = 2 if failed else 0
    except Exception as error:                                   # noqa: BLE001
        lines.append("  RUN FAILED: %s: %s" % (type(error).__name__, error))
        code = 1

    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n\n")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
