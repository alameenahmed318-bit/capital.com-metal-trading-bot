"""Security regression checks for AL-ZOOL customer/owner separation."""
import pathlib
import sys
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from unittest.mock import patch

ROOT=pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"marketplace"/"backend"))
import app
from client_access import public_static, minimal_terminal_decision, basic_settings, OWNER_READ_ENDPOINTS

class RoleSeparationTests(unittest.TestCase):
    def test_root_asset_whitelist_blocks_all_private_files(self):
        for url in (
            "/marketplace/backend/strategy_engine.py",
            "/marketplace/backend/app.py",
            "/mt5_client/ALZOOL_Central_Client.mq5",
            "/local_ai/ai_relay.py",
            "/.env.local.example",
            "/strategy-engine.js",
            "/docs/AL-ZOOL-CENTRAL-AI-ARCHITECTURE-2026-11-14.md",
            "/marketplace%2fbackend%2fapp.py",
            "/../render.yaml",
            "/README.md",
        ):
            with self.subTest(url=url):
                self.assertFalse(public_static(url))
        for url in ("/","/index.html","/prime.html","/app.js","/owner-strategy-loader.js"):
            self.assertTrue(public_static(url))

    def test_critical_analytics_require_owner(self):
        for path in ("/api/bot-advisors","/api/market/context",
                     "/api/backtest/history","/api/backtest/learning",
                     "/api/ai/actions","/api/ai/learning","/api/ai/council",
                     "/api/trade-management/settings","/api/opportunities/events"):
            self.assertIn(path,OWNER_READ_ENDPOINTS)

    def test_terminal_omits_models_and_strategy_description(self):
        old={"signal":"BUY","confidence":0.78,"strategy_id":88,"strategy_name":"Secret Strategy",
             "combination_id":14,"entry_reference_price":2500.0,"entry_atr":1.4,
             "execution_ready":True,"trading_enabled":True,
             "multi_model_council":{"votes":{"Qwen":{"reason_ar":"secret"}}},
             "council":{"families":{"x":"BUY"}},"family_votes":{"x":"BUY"},
             "authorization_id":"AZ-demo","bar_time":1763200000}
        cleaned=minimal_terminal_decision(old)
        self.assertEqual(cleaned["signal"],"BUY")
        self.assertEqual(cleaned["strategy_id"],88)
        self.assertNotIn("strategy_name",cleaned)
        self.assertNotIn("multi_model_council",cleaned)
        self.assertNotIn("council",cleaned)
        self.assertNotIn("family_votes",cleaned)

    def test_basic_settings_never_return_advanced_keys(self):
        full={"single_enabled":True,"single_profit_target":1.5,
              "group_enabled":False,"group_profit_target":5.0,
              "stop_loss_enabled":True,"stop_loss_mode":"ATR","stop_loss_value":1.8,
              "position_management":{"recovery_enabled":True,"mode":"AI"},
              "strategy_count":200}
        sanitized=basic_settings(full)
        self.assertEqual(sanitized["single_profit_target"],1.5)
        self.assertNotIn("position_management",sanitized)
        self.assertNotIn("strategy_count",sanitized)

    def test_static_http_hides_code_and_keeps_owner_asset_private(self):
        with ThreadingHTTPServer(("127.0.0.1",0),app.Handler) as server:
            t=threading.Thread(target=server.serve_forever,daemon=True)
            t.start()
            base=f"http://127.0.0.1:{server.server_port}"
            try:
                for name,status in (
                    ("/health",200),
                    ("/prime.html",200),
                    ("/marketplace/backend/strategy_engine.py",404),
                    ("/mt5_client/ALZOOL_Central_Client.mq5",404),
                    ("/strategy-engine.js",403),
                    ("/api/ai/council",403),
                ):
                    with self.subTest(name=name):
                        try:
                            with urllib.request.urlopen(base+name,timeout=3) as res:
                                actual=res.status
                        except urllib.error.HTTPError as error:
                            actual=error.code
                        self.assertEqual(actual,status)
            finally:
                server.shutdown();t.join(3)

    def test_owner_engine_uses_authenticated_browser_loader(self):
        js=(ROOT/"owner-strategy-loader.js").read_text(encoding="utf-8")
        for path in ("ai-analysis.html","backtest-lab.html","opportunities.html"):
            html=(ROOT/path).read_text(encoding="utf-8")
            self.assertIn("owner-strategy-loader.js",html)
            self.assertIn("ALZOOL_STRATEGY_READY",html)
            self.assertNotIn('<script src="/strategy-engine.js',html)
        self.assertIn('"/api/access/check"',js)
        self.assertIn('"/strategy-engine.js"',js)
        self.assertIn('role.role!=="owner"',js)

if __name__=="__main__":
    unittest.main()
