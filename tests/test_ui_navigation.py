"""Protect AL-ZOOL navigation links across all user-facing pages."""
from pathlib import Path
import re
import unittest

ROOT=Path(__file__).resolve().parents[1]
PAGES={
    "dashboard.html":["liveStatus","aiCouncilLive","tradingSettingsPanel","accountPerformance","closedHistory"],
    "trade-management.html":["positions","protection","autoClose","positionManagement","smartRecovery","pending","recoveryLog"],
    "ai-analysis.html":["aiResult","diagPanel"],
    "learning-lab.html":["strategyAuditPanel","liveStatus","runsTable","learningStatus"],
    "opportunities.html":["marketStatus","topStrategies","topCombinations","eventTable"],
    "backtest-lab.html":["methodNote","strategyResults","comboResults"],
    "trade-history.html":["historyStatus","tradesTable","tradeEventsTable"],
}
class NavigationTests(unittest.TestCase):
    def test_all_pages_use_single_navigation(self):
        for page in PAGES:
            with self.subTest(page=page):
                data=(ROOT/page).read_text(encoding="utf-8")
                self.assertIn("/ui-shell.js?v=nav-20261115-1",data)
                self.assertIn("/ui-navigation.css?v=nav-20261115-1",data)
                self.assertIn('class="sidebar"',data)
    def test_shortcut_anchors_exist_on_each_page(self):
        nav=(ROOT/"ui-shell.js").read_text(encoding="utf-8")
        for page,ids in PAGES.items():
            html=(ROOT/page).read_text(encoding="utf-8")
            with self.subTest(page=page):
                self.assertIn('"'+page+'":',nav)
                for item in ids:
                    self.assertIn('id="'+item+'"',html)
                    self.assertIn('["'+item+'",',nav)
    def test_nav_page_links_exist(self):
        nav=(ROOT/"ui-shell.js").read_text(encoding="utf-8")
        for p in PAGES:
            self.assertIn('["'+p+'",',nav)
            self.assertTrue((ROOT/p).is_file())
        self.assertIn('["","⚙","بوابة المالك والعملاء"',nav)
    def test_owner_navigation_uses_real_sections(self):
        html=(ROOT/"index.html").read_text(encoding="utf-8")
        script=(ROOT/"admin-navigation.js").read_text(encoding="utf-8")
        self.assertIn("/admin-navigation.js?v=nav-20261115-1",html)
        self.assertIn("/ui-navigation.css?v=nav-20261115-2",html)
        for section in ("adminOverview","adminClients","ownerPairPanel","councilPanel","actionPanel","learningPanel","versionsPanel"):
            self.assertIn('id="'+section+'"',html)
            self.assertIn('["'+section+'",',script)
if __name__=="__main__":
    unittest.main()
