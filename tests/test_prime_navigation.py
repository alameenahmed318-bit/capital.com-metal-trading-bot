"""Static regression checks for the AL-ZOOL PRIME unified navigation shell."""
from pathlib import Path
import unittest

ROOT=Path(__file__).resolve().parents[1]

class PrimeNavigationTests(unittest.TestCase):
    def test_prime_page_and_assets(self):
        html=(ROOT/"prime.html").read_text(encoding="utf-8")
        css=(ROOT/"prime.css").read_text(encoding="utf-8")
        js=(ROOT/"prime.js").read_text(encoding="utf-8")
        self.assertIn("/prime.js?v=sudan-20261115-2",html)
        self.assertIn("/prime.css?v=sudan-20261115-1",html)
        self.assertIn('id="primeFrame"',html)
        self.assertIn('id="primeNav"',html)
        self.assertIn("sudan-emblem",html)
        self.assertIn("علم السودان",html)
        self.assertIn("--sudan-red",css) if "--sudan-red" in css else self.assertIn("--red:#cf3d4a",css)
        self.assertIn("applyEmbeddedStyle",js)
        self.assertIn("attachInnerLinks",js)

    def test_embedded_modules_receive_prime_design(self):
        js=(ROOT/"prime.js").read_text(encoding="utf-8")
        css=(ROOT/"prime-embedded.css").read_text(encoding="utf-8")
        self.assertIn("az-prime-embedded-root",js)
        self.assertIn("/prime-embedded.css?v=sudan-20261115-2",js)
        for selector in (".az-prime-embedded-root",".card",".kpi",".az-admin-content"):
            self.assertIn(selector,css)
        self.assertIn("requested.split",js)

    def test_existing_connected_modules_remain_accessible(self):
        js=(ROOT/"prime.js").read_text(encoding="utf-8")
        for url in ("dashboard.html","trade-management.html","opportunities.html",
                    "ai-analysis.html","learning-lab.html","backtest-lab.html",
                    "trade-history.html"):
            with self.subTest(url=url):
                self.assertIn("/"+url,js)
                self.assertTrue((ROOT/url).is_file())

    def test_embedded_pages_allowed_only_from_same_origin(self):
        app=(ROOT/"marketplace"/"backend"/"app.py").read_text(encoding="utf-8")
        self.assertIn('self.send_header("X-Frame-Options","SAMEORIGIN")',app)
        self.assertIn("frame-ancestors 'self'",app)
        self.assertNotIn('self.send_header("X-Frame-Options","DENY")',app)

    def test_navigation_does_not_modify_execution_settings(self):
        js=(ROOT/"prime.js").read_text(encoding="utf-8")
        self.assertNotIn("/api/auto-trading",js)
        self.assertNotIn("/v1/terminal-action-claim",js)
        self.assertNotIn("localStorage.setItem",js)
        self.assertIn('cache:"no-store"',js)
        self.assertIn("from", "from")

if __name__=="__main__":
    unittest.main()
