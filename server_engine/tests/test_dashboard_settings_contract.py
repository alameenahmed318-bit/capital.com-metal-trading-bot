import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GATEWAY = (ROOT / "server_engine" / "supabase_gateway.py").read_text(encoding="utf-8")
DASHBOARD = (ROOT / "app.js").read_text(encoding="utf-8")
EA = (ROOT / "mt5_client" / "ALZOOL_Central_Client.mq5").read_text(encoding="utf-8")

# Dashboard controls own settings; only learning controls are server-only and never sent to MT5 as local inputs.
SERVER_ONLY_SETTINGS = {"learning_enabled", "learning_rate_percent"}
NOT_YET_IMPLEMENTED_IN_EA = SERVER_ONLY_SETTINGS


class DashboardSettingsContractTests(unittest.TestCase):
    def test_every_saved_setting_has_a_dashboard_control(self):
        defaults_line = GATEWAY.split("BASIC_SETTINGS_DEFAULTS = ", 1)[1].splitlines()[0]
        server_keys = set(re.findall(r'"([a-z_]+)"\s*:', defaults_line))
        ui_fields = set(re.findall(r'\["([a-z_]+)","[^"]+","(?:bool|number|select|strategy)"\]', DASHBOARD))
        self.assertEqual(server_keys, ui_fields)

    def test_only_explicit_server_side_learning_settings_are_not_parsed_by_mt5(self):
        defaults_line = GATEWAY.split("BASIC_SETTINGS_DEFAULTS = ", 1)[1].splitlines()[0]
        server_keys = set(re.findall(r'"([a-z_]+)"\s*:', defaults_line))
        missing = {key for key in server_keys if f'"{key}"' not in EA}
        self.assertEqual(missing, SERVER_ONLY_SETTINGS)
        self.assertIn('"learning_enabled"', GATEWAY)
        self.assertIn('"learning_rate_percent"', GATEWAY)

    def test_mt5_inputs_are_connector_credentials_only(self):
        inputs = set(re.findall(r'^input\s+\w+\s+(Inp\w+)', EA, re.MULTILINE))
        self.assertEqual(inputs, {
            "InpServerUrl", "InpEngineId", "InpEngineToken",
            "InpServerSymbol", "InpHttpTimeoutMs",
        })

    def test_recovery_can_follow_opposite_side_without_ordinary_entry_filters(self):
        self.assertIn('if(!recovery_order && direction=="BUY" && !g_cfg_allow_buy)', EA)
        self.assertIn('if(!recovery_order && direction=="SELL" && !g_cfg_allow_sell)', EA)
        self.assertIn('if(!recovery_order && g_cfg_max_spread_points>0', EA)
        self.assertIn('!GetATR(atr) && !recovery_order && !management_order', EA)

    def test_candle_follow_and_reversal_protection_are_wired(self):
        self.assertIn("void ManageCandleChase()", EA)
        self.assertIn("void ManageConfirmedReversal()", EA)
        self.assertIn("ManageCandleChase();", EA)
        self.assertIn("ManageConfirmedReversal();", EA)


if __name__ == "__main__":
    unittest.main()
