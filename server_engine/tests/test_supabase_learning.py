import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from server_engine.supabase_gateway import EngineGateway


class FakeSupabaseLearningGateway(EngineGateway):
    def __init__(self):
        super().__init__("https://example.supabase.co", "service-key")
        self.saved = {}
        self.weights = {}
        self.weight_writes = 0
        self.learning_settings = {"learning_enabled": True, "learning_rate_percent": 4.0}

    def settings_for_client(self, client_id, token):
        return dict(self.learning_settings)

    def _client(self, client_id, token):
        return {"user_id": "user-1"} if client_id == "client-1" and token == "token-1" else None

    def _request(self, method, path, payload=None, prefer=None):
        if method == "POST" and path == "rpc/alzool_record_learning_outcome":
            key = (payload["p_client_id"], payload["p_deal_id"])
            if key in self.saved:
                return {"accepted": True, "duplicate": True, "trained": False}
            self.saved[key] = dict(payload)
            sid = int(payload["p_strategy_id"])
            pnl = float(payload["p_pnl"])
            enabled = bool(payload["p_learning_enabled"])
            rate = float(payload["p_learning_rate"])
            before = self.weights.get(sid, 1.0)
            if enabled and pnl != 0:
                factor = 1.0 + rate if pnl > 0 else 1.0 - rate
                self.weights[sid] = max(0.25, min(4.0, before * factor))
                self.weight_writes += 1
            return {"accepted": True, "duplicate": False, "trained": enabled and pnl != 0,
                    "weight_before": before, "weight_after": self.weights.get(sid, before)}
        if method == "GET" and path.startswith("alzool_engine_weights?"):
            sid = int(path.split("strategy_id=eq.", 1)[1].split("&", 1)[0])
            return [{"weight": self.weights.get(sid, 1.0)}]
        if method == "POST" and path == "alzool_engine_weights":
            self.weights[int(payload["strategy_id"])] = float(payload["weight"])
            self.weight_writes += 1
            return []
        raise AssertionError((method, path, payload, prefer))


class SupabaseLearningPipelineTests(unittest.TestCase):
    def setUp(self):
        self.gateway = FakeSupabaseLearningGateway()

    def test_closed_position_trains_once_even_if_outcome_is_retried(self):
        payload = {"deal_id": "position-90001", "strategy_id": 12, "pnl": 5.25}
        first = self.gateway.outcome("client-1", "token-1", payload)
        second = self.gateway.outcome("client-1", "token-1", payload)
        self.assertTrue(first["accepted"])
        self.assertFalse(first["duplicate"])
        self.assertTrue(second["duplicate"])
        self.assertEqual(len(self.gateway.saved), 1)
        self.assertEqual(self.gateway.weight_writes, 1)
        self.assertAlmostEqual(self.gateway.weights[12], 1.04)

    def test_loss_reduces_weight_only_after_persisted_outcome(self):
        result = self.gateway.outcome(
            "client-1", "token-1",
            {"deal_id": "position-90002", "strategy_id": 27, "pnl": -3.0},
        )
        self.assertFalse(result["duplicate"])
        self.assertAlmostEqual(self.gateway.weights[27], 0.96)

    def test_learning_rate_is_configurable(self):
        self.gateway.learning_settings["learning_rate_percent"] = 10.0
        result = self.gateway.outcome(
            "client-1", "token-1",
            {"deal_id": "position-90003", "strategy_id": 31, "pnl": 2.0},
        )
        self.assertTrue(result["trained"])
        self.assertAlmostEqual(self.gateway.weights[31], 1.10)

    def test_disabled_learning_records_outcome_without_changing_weight(self):
        self.gateway.learning_settings["learning_enabled"] = False
        result = self.gateway.outcome(
            "client-1", "token-1",
            {"deal_id": "position-90004", "strategy_id": 32, "pnl": 2.0},
        )
        self.assertFalse(result["trained"])
        self.assertEqual(self.gateway.weight_writes, 0)
        self.assertEqual(len(self.gateway.saved), 1)

    def test_invalid_learning_payload_is_rejected(self):
        with self.assertRaises(ValueError):
            self.gateway.outcome("client-1", "token-1", None)
        with self.assertRaises(ValueError):
            self.gateway.outcome(
                "client-1", "token-1",
                {"deal_id": "bad", "strategy_id": 0, "pnl": 1.0},
            )


class SettingsBeforeCredentialsGateway(EngineGateway):
    def __init__(self):
        super().__init__("https://example.supabase.co", "service-key")
        self.client = None
        self.settings = {}

    def _request(self, method, path, payload=None, prefer=None):
        if method == "GET" and path.startswith("alzool_engine_clients?user_id=eq."):
            if self.client is None:
                return []
            if "select=trading_settings" in path:
                return [{"trading_settings": self.settings}]
            return [{"client_id": self.client["client_id"]}]
        if method == "POST" and path == "alzool_engine_clients":
            self.client = dict(payload)
            return [dict(payload)]
        if method == "POST" and path == "alzool_engine_weights":
            return []
        if method == "PATCH" and path.startswith("alzool_engine_clients?user_id=eq."):
            self.settings = dict(payload["trading_settings"])
            return []
        raise AssertionError((method, path, payload, prefer))


class DashboardSettingsPersistenceTests(unittest.TestCase):
    def test_settings_saved_before_credentials_are_not_lost(self):
        gateway = SettingsBeforeCredentialsGateway()
        saved = gateway.save_settings_for_user("user-before-mt5", {"lot": 0.02})
        self.assertIsNotNone(gateway.client)
        self.assertAlmostEqual(gateway.settings["lot"], 0.02)
        self.assertAlmostEqual(saved["lot"], 0.02)
        self.assertEqual(saved["recovery_direction_mode"], "SAME")
        self.assertNotIn("recovery_max_entries", saved)


if __name__ == "__main__":
    unittest.main()
