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

    def _client(self, client_id, token):
        return {"user_id": "user-1"} if client_id == "client-1" and token == "token-1" else None

    def _request(self, method, path, payload=None, prefer=None):
        if method == "POST" and path == "alzool_engine_outcomes":
            key = (payload["client_id"], payload["deal_id"])
            if key in self.saved:
                return []
            self.saved[key] = dict(payload)
            return [dict(payload)]
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

    def test_invalid_learning_payload_is_rejected(self):
        with self.assertRaises(ValueError):
            self.gateway.outcome("client-1", "token-1", None)
        with self.assertRaises(ValueError):
            self.gateway.outcome(
                "client-1", "token-1",
                {"deal_id": "bad", "strategy_id": 0, "pnl": 1.0},
            )


if __name__ == "__main__":
    unittest.main()
