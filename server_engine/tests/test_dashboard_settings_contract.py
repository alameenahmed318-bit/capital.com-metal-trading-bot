import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GATEWAY = (ROOT / "server_engine" / "supabase_gateway.py").read_text(encoding="utf-8")
MANAGEMENT = (ROOT / "server_engine" / "management.py").read_text(encoding="utf-8")
DASHBOARD = (ROOT / "app.js").read_text(encoding="utf-8")
EA = (ROOT / "mt5_client" / "ALZOOL_Central_Client.mq5").read_text(encoding="utf-8")


class DashboardSettingsContractTests(unittest.TestCase):
    def test_every_saved_setting_has_a_dashboard_control(self):
        defaults_line = GATEWAY.split("BASIC_SETTINGS_DEFAULTS = ", 1)[1].splitlines()[0]
        server_keys = set(re.findall(r'"([a-z_]+)"\\s*:', defaults_line))
        ui_fields = set(re.findall(r'\\["([a-z_]+)","[^"]+","(?:bool|number|select|strategy)"\\]', DASHBOARD))
        self.assertEqual(server_keys, ui_fields)

    def test_central_planner_owns_core_trade_decisions(self):
        for key in (
            "allow_buy", "allow_sell", "lot", "max_spread_points",
            "one_direction_only", "max_positions", "global_loss_enabled",
            "global_loss_amount", "global_profit_enabled", "profit_target",
            "per_trade_profit_enabled", "per_trade_profit_target",
            "fixed_sl_enabled", "fixed_sl_distance", "trailing_enabled",
            "trail_start", "trail_distance", "recovery_enabled",
            "recovery_min_positions", "recovery_trigger_loss",
            "recovery_target_profit", "recovery_min_confidence",
            "reverse_protection_enabled", "reverse_trigger", "reverse_continue",
            "ai_loss_guard_enabled", "candle_chase_enabled",
            "candle_chase_min_move", "pending_distance",
        ):
            self.assertIn(key, MANAGEMENT)
        self.assertIn('configured_mode=str(dashboard_settings.get("strategy_mode"', GATEWAY)

    def test_mt5_inputs_are_connection_credentials_only(self):
        inputs = set(re.findall(r'^input\\s+\\w+\\s+(Inp\\w+)', EA, re.MULTILINE))
        self.assertEqual(inputs, {
            "InpServerUrl", "InpEngineId", "InpEngineToken", "InpHttpTimeoutMs",
        })

    def test_mt5_market_poll_does_not_open_or_manage_trades_locally(self):
        start = EA.index("void PollCentralEngine()")
        end = EA.index("\\nint OnInit()", start)
        market_poll = EA[start:end]
        self.assertIn("RequestCentralDecision(", market_poll)
        self.assertNotIn("PlaceCentralTrade(", market_poll)
        self.assertNotIn("CheckTradingControl(", market_poll)
        self.assertNotIn("PollCentralSettings(", market_poll)

    def test_timer_executes_only_signed_central_management_commands(self):
        start = EA.index("void OnTimer()")
        end = EA.index("\\nvoid OnTick()", start)
        timer = EA[start:end]
        self.assertIn("PollCentralManagement();", timer)
        self.assertNotIn("ManageCandleChase();", timer)
        self.assertNotIn("ManageConfirmedReversal();", timer)
        self.assertNotIn("ManageAiLossGuard();", timer)

    def test_server_owns_recovery_addon_cooling_and_candle_follow(self):
        for key in (
            "recovery_enabled", "recovery_min_positions", "recovery_trigger_loss",
            "recovery_target_profit", "addon_enabled", "addon_step", "addon_multiplier",
            "cooling_enabled", "cooling_step", "cooling_multiplier",
            "candle_chase_enabled", "candle_chase_min_move",
            "reverse_protection_enabled", "ai_loss_guard_enabled",
        ):
            self.assertIn(key, MANAGEMENT)
        for action in ("OPEN_MARKET", "PLACE_PENDING", "CANCEL_PENDING", "CLOSE_POSITION", "CLOSE_ALL", "MODIFY_SL"):
            self.assertIn(f'"{action}"', MANAGEMENT)

    def test_ea_verifies_and_executes_signed_commands(self):
        self.assertIn("VerifyManagementCommand(response)", EA)
        self.assertIn("HMAC_SHA256_HEX(signed_payload,InpEngineToken,expected)", EA)
        self.assertIn('action=="OPEN_MARKET"', EA)
        self.assertIn('action=="PLACE_PENDING"', EA)
        self.assertIn('action=="CANCEL_PENDING"', EA)


if __name__ == "__main__":
    unittest.main()
