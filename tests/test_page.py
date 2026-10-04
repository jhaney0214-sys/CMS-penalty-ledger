"""Drive the rendered page and read what a visitor would see.

    python tests/test_page.py

The numbers are checked against docs/data/meta.json rather than against
constants, so the suite stays true as the monthly captures move them. What
is checked is the wiring: that the page prints the data's figures, carries
cms_ledger's refusal sentences word for word, and never reports "nothing
dropped" from a single capture.

Needs playwright and a chromium (`python -m playwright install chromium`);
skips cleanly without them.
"""

import contextlib
import functools
import http.server
import json
import pathlib
import threading
import unittest

DOCS = pathlib.Path(__file__).resolve().parent.parent / "docs"
META = json.loads((DOCS / "data" / "meta.json").read_text(encoding="utf-8"))

try:
    from playwright.sync_api import sync_playwright
    with sync_playwright() as _pw:
        HAVE_BROWSER = pathlib.Path(_pw.chromium.executable_path).exists()
except Exception:                                                # noqa: BLE001
    HAVE_BROWSER = False


@contextlib.contextmanager
def serve(directory):
    handler = functools.partial(http.server.SimpleHTTPRequestHandler,
                                directory=str(directory))
    handler.log_message = lambda *a: None
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield "http://127.0.0.1:%d/" % server.server_address[1]
    finally:
        server.shutdown()
        server.server_close()


def fmt(n):
    return format(n, ",d")


@unittest.skipUnless(HAVE_BROWSER, "playwright or chromium not available")
class TestPage(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls._serve = serve(DOCS)
        cls.url = cls._serve.__enter__()
        cls._pw = sync_playwright().start()
        cls.browser = cls._pw.chromium.launch()
        cls.page = cls.browser.new_page()
        cls.errors = []
        cls.page.on("pageerror", lambda e: cls.errors.append(str(e)))

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls._pw.stop()
        cls._serve.__exit__(None, None, None)

    def view(self, fragment=""):
        self.page.goto(self.url + ("#" + fragment if fragment else ""))
        self.page.wait_for_function(
            "!document.querySelector('#out').textContent.includes('Loading')")
        self.page.wait_for_timeout(300)
        return self.page.inner_text("#out")

    def tearDown(self):
        self.assertEqual(self.errors, [])

    def test_home_carries_the_denominator_and_the_refusal(self):
        text = self.view()
        self.assertIn("%s of %s" % (fmt(META["penalised"]),
                                    fmt(META["surveyed"])), text)
        self.assertIn(META["text"]["none_penalised"], text)
        self.assertIn("$" + fmt(META["fine_total"]), text)

    def test_a_state_matches_its_exported_totals(self):
        s = META["states"]["IL"]
        text = self.view("state=IL")
        self.assertIn("%s of %s" % (fmt(s["penalised"]), fmt(s["surveyed"])),
                      text)
        self.assertIn("$" + fmt(s["fine_total"]), text)

    def test_an_unpenalised_facility_is_not_called_clean(self):
        index = json.loads((DOCS / "data" / "index.json").read_text(
            encoding="utf-8"))
        ccn = next(row[0] for row in index if not row[4])
        text = self.view("ccn=" + ccn)
        self.assertIn(META["text"]["no_penalty"], text)

    def test_one_capture_is_nothing_to_compare_not_nothing_dropped(self):
        text = self.view("ccn=015019")
        if len(META["captures"]) < 2 and not META.get("history"):
            self.assertIn("Nothing to compare yet", text)
        else:
            self.assertNotIn("Nothing to compare yet", text)

    def test_a_penalty_cms_dropped_is_shown_as_aged_out_not_reversed(self):
        """Merry Wood's 2019-03-02 fine left CMS's file after 2022-03-27; the
        rebuilt history is the only place it still appears."""
        if not META.get("history"):
            self.skipTest("no rebuilt history in docs/data")
        text = self.view("ccn=015019")
        self.assertIn("that is not a reversal", text)
        self.assertIn("2019-03-02", text)
        self.assertIn("$78,676", text)

    def test_an_unknown_ccn_is_not_an_empty_record(self):
        text = self.view("ccn=999999")
        self.assertIn("do not list this number at all", text)
        self.assertNotIn(META["text"]["no_penalty"], text)

    def test_a_ccn_missing_its_leading_zero_still_finds_it(self):
        text = self.view("q=15019")
        self.assertIn("CCN 015019", text)

    def test_nothing_overflows_a_phone(self):
        self.page.set_viewport_size({"width": 320, "height": 800})
        try:
            self.view("ccn=015019")
            self.assertFalse(self.page.evaluate(
                "document.documentElement.scrollWidth > innerWidth"))
        finally:
            self.page.set_viewport_size({"width": 1280, "height": 900})


if __name__ == "__main__":
    unittest.main()


class TestContentSecurityPolicy(unittest.TestCase):
    """docs/_headers allows index.html's inline script by its hash. Edit the
    script without updating the hash and the live search stops working, with
    no error anywhere but the visitor's browser console (2026-10-03)."""

    def test_every_inline_script_is_allowed_by_its_hash(self):
        import base64
        import hashlib
        import re
        headers = (DOCS / "_headers").read_text(encoding="utf-8")
        for page in [DOCS / "index.html"] + sorted((DOCS / "facilities").glob("*.html"))[:50]:
            for block in re.findall(rb"<script>(.*?)</script>", page.read_bytes(), re.S):
                digest = base64.b64encode(hashlib.sha256(block).digest()).decode()
                self.assertIn("'sha256-%s'" % digest, headers,
                              "%s: an inline script changed; update its hash in docs/_headers" % page.name)
