"""Read-only, trade-safe diagnostics for central add-on/cooling prerequisites."""
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"marketplace"/"backend"))
from system_readiness import position_management_status, owner_readiness


class PositionManagementReadinessTests(unittest.TestCase):
    def setUp(self):
        self.now=datetime.now(timezone.utc)
        self.snapshot={"received_at":self.now,
                       "mt5_central_trading_enabled":True,
                       "mt5_position_management_enabled":True}
        self.settings={"trading_enabled":True,"position_management":{
            "central_actions_enabled":True,"addon_enabled":True,
            "cooling_enabled":True,"mode":"AI"}}

    def check(self,snapshot=None,settings=None):
        return position_management_status(
            self.snapshot if snapshot is None else snapshot,
            self.settings if settings is None else settings,
            now=self.now)

    def test_all_switches_on_only_enables_polling_not_execution(self):
        info=self.check()
        self.assertEqual(info["status"],"READY_TO_POLL")
        self.assertTrue(info["polling_ready"])
        self.assertFalse(info["execution_authorized"])

    def test_ea_management_off_is_reported_not_silently_ignored(self):
        info=self.check(snapshot={**self.snapshot,"mt5_position_management_enabled":False})
        self.assertEqual(info["status"],"BLOCKED")
        self.assertIn("InpEnableCentralPositionManagement",info["reason_ar"])
        self.assertIn("OFF",info["reason_ar"])

    def test_ea_trade_off_even_if_dashboard_server_on(self):
        info=self.check(snapshot={**self.snapshot,"mt5_central_trading_enabled":False})
        self.assertFalse(info["polling_ready"])
        self.assertIn("InpEnableCentralTrading",info["reason_ar"])

    def test_missing_ea_flags_does_not_claim_enabled(self):
        info=self.check(snapshot={"received_at":self.now})
        self.assertFalse(info["polling_ready"])
        self.assertIsNone(info["mt5_position_management_enabled"])
        self.assertIn("غير معروف",info["reason_ar"])

    def test_old_snapshot_not_ready(self):
        info=self.check(snapshot={**self.snapshot,
                                 "received_at":self.now-timedelta(seconds=35)})
        self.assertFalse(info["mt5_connected"])
        self.assertFalse(info["polling_ready"])

    def test_server_control_remains_independent_from_ea(self):
        settings={"trading_enabled":True,"position_management":{
            **self.settings["position_management"],"central_actions_enabled":False}}
        info=self.check(settings=settings)
        self.assertFalse(info["polling_ready"])
        self.assertIn("إجراءات الإدارة المركزية OFF",info["reason_ar"])

    def test_disabled_addons_and_cooling_explain_block(self):
        settings={"trading_enabled":True,"position_management":{
            **self.settings["position_management"],
            "addon_enabled":False,"cooling_enabled":False}}
        info=self.check(settings=settings)
        self.assertFalse(info["polling_ready"])
        self.assertIn("التعزيز والتبريد كلاهما OFF",info["reason_ar"])

    def test_readiness_contains_diagnostics_without_executing(self):
        data=owner_readiness({"revoked":False,"client_id":"redacted"},
            {**self.snapshot,"strategy_decision":{}},self.settings,{},None,now=self.now)
        info=data["position_management_status"]
        self.assertTrue(info["polling_ready"])
        self.assertFalse(info["execution_authorized"])


if __name__=="__main__":
    unittest.main()
