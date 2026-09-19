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
        if len(META["captures"]) < 2:
            self.assertIn("Nothing to compare yet", text)
        else:
            self.assertNotIn("Nothing to compare yet", text)

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
