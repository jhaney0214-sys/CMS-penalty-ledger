#!/usr/bin/env python3
"""One page per nursing home: every penalty CMS has published against it,
including the ones its current file no longer shows, each tied to the
archived edition that published it.

    python tools/facility_pages.py              # the 30 homes with the most in dropped fines
    python tools/facility_pages.py --top 50
    python tools/facility_pages.py --ccn 105407 --ccn 015411
    python tools/facility_pages.py --all        # every inspected home with a dropped fine

Writes docs/facilities/<ccn>-<name>.html, docs/facilities/index.html and one
page per state; with --all, also docs/sitemap.xml and docs/robots.txt, and it
removes pages for homes no longer in the set.

Widened to --all on 2026-09-28: a lawyer searches for one defendant, so thirty
pages could not show whether anyone looks. A page carries the edition it was
built through, not the day, so a rebuild with no new edition changes nothing.

Written 2026-09-27 as a demand test: whether people looking up a home, and
the lawyers who sue them, find and read a complete history. Two things set it
apart from what is already free, both found comparing Siesta Key (105407)
with NursingHomeNews.org that day. Some penalties CMS no longer shows are
missing elsewhere, including the largest. And CMS revises amounts while a
penalty is listed: that home's $799,880 fine was first published as
$125,970, which is the figure the other site still shows. So every row here
carries its first and last published amount and the editions that listed it.

A page states records, not conclusions. A penalty leaving CMS's file almost
always means it aged out of the three-year window; one that left early is
flagged, and the page says the file does not give a reason.
"""

import argparse
import collections
import datetime
import html
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import cms_ledger                                             # noqa: E402
import findings                                               # noqa: E402

OUT = os.path.join(ROOT, "docs", "facilities")
MANIFEST = os.path.join(ROOT, "snapshots", "cms-archive", "manifest.json")
MAIN_PAGE = os.path.join(ROOT, "docs", "index.html")
DOCS = os.path.join(ROOT, "docs")
SITE = "https://penalty-ledger.pages.dev"
FACILITY_FILE = re.compile(r"^[0-9A-Z]{6}-[a-z0-9-]*\.html$")


def editions(path=MANIFEST):
    """{archive date: the CMS URL of that edition's ZIP}, oldest first."""
    with open(path, encoding="utf-8") as handle:
        manifest = json.load(handle)
    return dict(sorted((a["archive_date"], a["archive"]) for a in manifest["archives"]))


def next_edition(dates, after):
    """The first edition dated after `after`: the one a penalty was gone from."""
    later = [d for d in dates if d > after]
    return later[0] if later else None


SMALL_WORDS = frozenset("a an and at by for in of on or the to".split())
KEEP_UPPER = frozenset("LLC LP LLP II III IV".split())


def display(name):
    """CMS's capitals as a title: 'MORGANTOWN HEIGHTS OF JOURNEY' reads
    'Morgantown Heights of Journey', not '... Of Journey'."""
    words = name.split()
    out = []
    for i, word in enumerate(words):
        if word.upper() in KEEP_UPPER:
            out.append(word.upper())
        elif i and word.lower() in SMALL_WORDS:
            out.append(word.lower())
        else:
            out.append(word[:1].upper() + word[1:].lower())
    return " ".join(out)


def slug(text):
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:60]


def page_name(ccn, name):
    return "%s-%s.html" % (ccn, slug(name))


def money(value):
    return "$" + "{:,.0f}".format(float(value))


def dropped_fine_dollars(rows):
    """{ccn: dollars of fines no longer in CMS's current file}."""
    out = collections.defaultdict(float)
    for r in rows:
        if r["in_latest"] == "no" and r["kind"] == "Fine" and r["amount"]:
            out[r["ccn"]] += float(r["amount"])
    return out


def choose(rows, surveyed, top):
    """The homes CMS inspects today with the most in dropped fines; all of
    them when `top` is None."""
    dollars = dropped_fine_dollars(rows)
    ranked = sorted((c for c in dollars if c in surveyed and dollars[c] > 0),
                    key=lambda c: (-dollars[c], c))
    return ranked if top is None else ranked[:top]


def style():
    """The main page's own stylesheet, so the two cannot drift apart."""
    with open(MAIN_PAGE, encoding="utf-8") as handle:
        text = handle.read()
    css = text[text.index("<style>") + len("<style>"):text.index("</style>")]
    # The main page's comments explain choices made for it, including one
    # from when it was meant not to be found; these pages are meant to be.
    return re.sub(r"/\*.*?\*/", "", css, flags=re.S)


def _early(row):
    return row["in_latest"] == "no" and findings._years(row) < findings.CLIFF_YEARS


def row_html(r, urls, today_name):
    dates = list(urls)
    link = lambda d: '<a href="%s">%s</a>' % (html.escape(urls[d]), d) if d in urls else d  # noqa: E731
    if r["kind"] == "Fine":
        what = money(r["amount"]) if r["amount"] else "amount not given"
        if r["first_amount"] and r["first_amount"] != r["amount"]:
            what += '<br><span class="meta">first published as %s; revised %s time%s</span>' % (
                money(r["first_amount"]), r["revisions"], "" if r["revisions"] == "1" else "s")
    else:
        what = "payment denial"
        if r["denial_days"]:
            what += ", %s days" % r["denial_days"]
        if r["denial_start"]:
            what += " from %s" % r["denial_start"]
    listed = "%s to %s<br><span class=\"meta\">%s edition%s</span>" % (
        link(r["first_seen"]), link(r["last_seen"]), r["appearances"],
        "" if r["appearances"] == "1" else "s")
    if r["gaps"] not in ("", "0"):
        listed += '<br><span class="meta">absent from %s edition%s in between</span>' % (
            r["gaps"], "" if r["gaps"] == "1" else "s")
    if r["in_latest"] == "yes":
        status = "Still published"
    else:
        gone = next_edition(dates, r["last_seen"])
        status = "Not shown since %s" % (link(gone) if gone else "the last edition")
        if _early(r):
            status += ('<br><span class="meta">left %.1f years after its date, before the '
                       'usual three; the file does not say why</span>' % findings._years(r))
    name = "" if r["name"] == today_name else '<br><span class="meta">as %s</span>' % html.escape(display(r["name"]))
    return ("<tr><td><time>%s</time>%s</td><td>%s</td><td class=\"num\">%s</td><td>%s</td><td>%s</td></tr>"
            % (r["date"], name, html.escape(r["kind"]), what, listed, status))


def facility_page(ccn, history, urls, today, generated):
    """One home's page. `history` is every archive row for the CCN."""
    rows = sorted(history, key=lambda r: (r["date"], r["kind"]), reverse=True)
    name, city, state = today["name"], today["city"], today["state"]
    title = "%s, %s, %s" % (display(name), display(city), state)
    fines = [r for r in rows if r["kind"] == "Fine" and r["amount"]]
    dropped = [r for r in fines if r["in_latest"] == "no"]
    shown = [r for r in rows if r["in_latest"] == "yes"]
    names = []
    for r in sorted(rows, key=lambda r: r["first_seen"]):
        if r["name"] not in names:
            names.append(r["name"])
    earliest = min(r["date"] for r in rows)
    summary = ("CMS's current file shows %d penalt%s for this home. Its archive of past editions "
               "holds %d, back to %s, including %d fine%s totalling %s that the current file no "
               "longer shows." % (
                   len(shown), "y" if len(shown) == 1 else "ies", len(rows), earliest[:4],
                   len(dropped), "" if len(dropped) == 1 else "s",
                   money(sum(float(r["amount"]) for r in dropped))))
    revised = [r for r in fines if r["first_amount"] and r["first_amount"] != r["amount"]]
    other_names = [n for n in names if n != name]
    parts = [
        "<!DOCTYPE html>",
        '<html lang="en"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        "<title>%s: every CMS penalty since %s</title>" % (html.escape(title), earliest[:4]),
        '<meta name="description" content="%s">' % html.escape(summary),
        '<link rel="stylesheet" href="style.css">',
        '<link rel="canonical" href="%s/facilities/%s">' % (SITE, page_name(ccn, name)[:-5]),
        '</head><body><main class="page">',
        '<p class="meta"><a href="../">Nursing Home Penalty Ledger</a> &rsaquo; <a href="./">Homes</a>'
        ' &rsaquo; <a href="%s">%s</a></p>' % (state_page(state), state),
        "<h1>%s</h1>" % html.escape(display(name)),
        "<p>%s, %s &middot; CMS certification number %s</p>" % (html.escape(display(city)), state, ccn),
        "<p>%s</p>" % html.escape(summary, quote=False),
    ]
    if other_names:
        parts.append("<p><strong>Earlier names in CMS's files:</strong> %s.</p>" % ", ".join(
            html.escape(display(n)) for n in other_names))
    if revised:
        parts.append("<p><strong>%d fine%s changed amount while CMS listed %s.</strong> Each row shows the "
                     "amount as first and as last published; sources quoting only one of them will "
                     "disagree with each other.</p>" % (
                         len(revised), "" if len(revised) == 1 else "s", "it" if len(revised) == 1 else "them"))
    parts += [
        "<h2>Every penalty in CMS's archive</h2>",
        '<div class="table-wrap"><table><thead><tr><th>Date</th><th>Kind</th><th class="num">Amount</th>'
        "<th>Listed by CMS</th><th>Now</th></tr></thead><tbody>",
        "\n".join(row_html(r, urls, name) for r in rows),
        "</tbody></table></div>",
        "<h2>What this is, and what it is not</h2>",
        "<p>CMS publishes nursing-home penalties as a rolling three-year window. When a penalty "
        "turns three it leaves the current file, and Care Compare and the tools built on it stop "
        "showing it. CMS keeps monthly archive copies of the whole dataset, and this page is built "
        "from every one of them since January 2019. Each date under <em>Listed by CMS</em> links to "
        "the archived edition that listed the penalty, so any row can be checked against CMS's own "
        "file.</p>",
        "<p>A penalty that is no longer shown was, in almost every case, not withdrawn: it aged out. "
        "One that left early is marked, and the file does not say why; appeals, settlements and "
        "corrections are all possible. Amounts are as CMS published them. Payment denials are counted "
        "in days, not dollars. This page states records; it is not legal advice and makes no finding "
        "about the care this home provides.</p>",
        '<p class="meta">Built from CMS\'s archive through the %s edition by '
        '<code>tools/facility_pages.py</code> in <a href="https://github.com/jhaney0214-sys/cms-penalty-ledger">'
        "cms-penalty-ledger</a>. Data: Centers for Medicare &amp; Medicaid Services, Provider Data "
        "Catalog, public domain.</p>" % list(urls)[-1],
        "</main></body></html>",
    ]
    return "\n".join(parts) + "\n"


def state_page(state):
    return "state-%s.html" % state.lower()


def _head(title, description, canonical):
    return [
        "<!DOCTYPE html>",
        '<html lang="en"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        "<title>%s</title>" % html.escape(title),
        '<meta name="description" content="%s">' % html.escape(description),
        '<link rel="stylesheet" href="style.css">',
        '<link rel="canonical" href="%s/facilities/%s">' % (SITE, canonical),
        '</head><body><main class="page">',
    ]


def _table(entries):
    rows = "\n".join(
        '<tr><td><a href="%s">%s</a></td><td>%s, %s</td><td class="num">%d</td><td class="num">%s</td></tr>'
        % (html.escape(e["page"]), html.escape(display(e["name"])), html.escape(display(e["city"])),
           e["state"], e["dropped"], money(e["dollars"])) for e in entries)
    return ['<div class="table-wrap"><table><thead><tr><th>Home</th><th>Where</th>'
            '<th class="num">Fines no longer shown</th><th class="num">Total</th></tr></thead><tbody>',
            rows, "</tbody></table></div>"]


def _count(n):
    return "{:,}".format(n)


def index_page(entries, edition, top=100):
    ranked = sorted(entries, key=lambda e: (-e["dollars"], e["page"]))
    states = collections.Counter(e["state"] for e in entries)
    parts = _head("Nursing homes with the largest fines CMS no longer shows",
                  "Full CMS penalty histories, including fines older than the three years Care "
                  "Compare shows, for %s nursing homes." % _count(len(entries)), "")
    parts += [
        '<p class="meta"><a href="../">Nursing Home Penalty Ledger</a></p>',
        "<h1>The fines CMS no longer shows</h1>",
        "<p>%s nursing home%s CMS inspects today ha%s fines its current file has dropped. Each "
        "page lists every penalty in CMS's archive since 2019, with the edition that published "
        "it.</p>" % (_count(len(entries)), "" if len(entries) == 1 else "s",
                     "s" if len(entries) == 1 else "ve"),
        "<h2>By state</h2>",
        "<p>%s</p>" % " &middot; ".join('<a href="%s">%s</a> (%s)' % (state_page(st), st, _count(n))
                                        for st, n in sorted(states.items())),
    ]
    if len(ranked) > top:
        parts.append("<h2>The %d largest</h2>" % top)
    parts += _table(ranked[:top])
    parts += ['<p class="meta">Built from CMS\'s archive through the %s edition by '
              '<code>tools/facility_pages.py</code>. Data: CMS, public domain.</p>' % edition,
              "</main></body></html>"]
    return "\n".join(parts) + "\n"


def state_index(state, entries, edition):
    ranked = sorted(entries, key=lambda e: (-e["dollars"], e["page"]))
    parts = _head("%s nursing homes: fines CMS no longer shows" % state,
                  "Full CMS penalty histories for %s nursing homes in %s, including fines older "
                  "than the three years Care Compare shows." % (_count(len(entries)), state),
                  state_page(state)[:-5])
    parts += [
        '<p class="meta"><a href="../">Nursing Home Penalty Ledger</a> &rsaquo; <a href="./">Homes</a></p>',
        "<h1>%s: fines CMS no longer shows</h1>" % state,
        "<p>Nursing homes in %s that CMS inspects today with fines its current file has dropped, "
        "largest first: %s.</p>" % (state, _count(len(entries))),
    ]
    parts += _table(ranked)
    parts += ['<p class="meta">Built from CMS\'s archive through the %s edition. Data: CMS, public '
              'domain.</p>' % edition, "</main></body></html>"]
    return "\n".join(parts) + "\n"


def sitemap(pages, edition):
    """Clean URLs, as Cloudflare Pages serves them: /x.html redirects to /x."""
    urls = ["%s/" % SITE] + ["%s/facilities/%s" % (SITE, p[:-5] if p.endswith(".html") else p)
                             for p in pages]
    body = "\n".join("<url><loc>%s</loc><lastmod>%s</lastmod></url>" % (html.escape(u), edition)
                     for u in urls)
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n%s\n</urlset>\n' % body)


ROBOTS = "User-agent: *\nAllow: /\n\nSitemap: %s/sitemap.xml\n" % SITE


def _write(path, text):
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)


def build(ccns=None, top=30, out=OUT, docs=DOCS, every=False):
    rows = findings.read_history()
    capture = cms_ledger.load()
    by_ccn = collections.defaultdict(list)
    for r in rows:
        by_ccn[r["ccn"]].append(r)
    chosen = ccns or choose(rows, capture.facilities(), None if every else top)
    urls = editions()
    edition = list(urls)[-1]
    dollars = dropped_fine_dollars(rows)
    os.makedirs(out, exist_ok=True)
    _write(os.path.join(out, "style.css"), style() + "\n.meta{font-size:.85em;color:var(--ink-muted);}\n")
    entries = []
    for ccn in chosen:
        today = cms_ledger.facility(capture, ccn)
        if today is None or not by_ccn[ccn]:
            raise SystemExit("%s: not in the current capture or the archive" % ccn)
        page = page_name(ccn, today["name"])
        _write(os.path.join(out, page), facility_page(ccn, by_ccn[ccn], urls, today, edition))
        entries.append({"page": page, "name": today["name"], "city": today["city"],
                        "state": today["state"], "dollars": dollars.get(ccn, 0.0),
                        "dropped": sum(1 for r in by_ccn[ccn]
                                       if r["in_latest"] == "no" and r["kind"] == "Fine")})
    _write(os.path.join(out, "index.html"), index_page(entries, edition))
    by_state = collections.defaultdict(list)
    for e in entries:
        by_state[e["state"]].append(e)
    for state, group in by_state.items():
        _write(os.path.join(out, state_page(state)), state_index(state, group, edition))
    if every:
        keep = {e["page"] for e in entries} | {state_page(s) for s in by_state}
        for name in os.listdir(out):
            if name not in keep and (FACILITY_FILE.match(name) or name.startswith("state-")):
                os.remove(os.path.join(out, name))
        pages = [""] + sorted(state_page(s) for s in by_state) + sorted(e["page"] for e in entries)
        _write(os.path.join(docs, "sitemap.xml"), sitemap(pages, edition))
        _write(os.path.join(docs, "robots.txt"), ROBOTS)
    return entries


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--top", type=int, default=30)
    parser.add_argument("--ccn", action="append", help="build these homes instead of the top list")
    parser.add_argument("--all", action="store_true",
                        help="every inspected home with a dropped fine, plus sitemap.xml and robots.txt")
    args = parser.parse_args(argv)
    ccns = [cms_ledger.normalise_ccn(c) for c in args.ccn] if args.ccn else None
    entries = build(ccns, args.top, every=args.all and not ccns)
    print("%d pages -> %s" % (len(entries), os.path.relpath(OUT, ROOT)))
    for e in entries[:5]:
        print("  %-50s %s" % (e["name"][:50], money(e["dollars"])))


if __name__ == "__main__":
    main()
