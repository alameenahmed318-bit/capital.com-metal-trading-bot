import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GATEWAY = (ROOT / "server_engine" / "supabase_gateway.py").read_text(encoding="utf-8")
DASHBOARD = (ROOT / "app.js").read_text(encoding="utf-8")
EA = (ROOT / "mt5_client" / "ALZOOL_Central_Client.mq5").read_text(encoding="utf-8")

# Every saved trading control must be parsed by the MT5 execution adapter.
NOT_YET_IMPLEMENTED_IN_EA = set()


class DashboardSettingsContractTests(unittest.TestCase):
    def test_every_saved_setting_has_a_dashboard_control(self):
        defaults_line = GATEWAY.split("BASIC_SETTINGS_DEFAULTS = ", 1)[1].splitlines()[0]
        server_keys = set(re.findall(r'"([a-z_]+)"\s*:', defaults_line))
        ui_fields = set(re.findall(r'\["([a-z_]+)","[^"]+","(?:bool|number|select|strategy)"\]', DASHBOARD))
        self.assertEqual(server_keys, ui_fields)

    def test_every_non_deferred_setting_is_consumed_by_mt5_client(self):
        defaults_line = GATEWAY.split("BASIC_SETTINGS_DEFAULTS = ", 1)[1].splitlines()[0]
        server_keys = set(re.findall(r'"([a-z_]+)"\s*:', defaults_line))
        missing = {key for key in server_keys if f'"{key}"' not in EA}
        self.assertEqual(missing, NOT_YET_IMPLEMENTED_IN_EA)

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
