import hashlib
import json
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import service


class OutcomeApiTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.old_db_path = service.DB_PATH
        service.DB_PATH = Path(self.temp_dir.name) / "test.sqlite3"
        service.init_db()

        self.client_id = "outcome-test-client"
        self.token = "test-client-token-secret"
        with service.connect_db() as db:
            db.execute(
                "INSERT INTO clients(client_id,token_hash,expires_at,revoked) VALUES(?,?,?,0)",
                (self.client_id, hashlib.sha256(self.token.encode()).hexdigest(), time.time() + 3600),
            )

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), service.Handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.httpd.server_port}/v1/outcome/{self.client_id}"

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=2)
        service.DB_PATH = self.old_db_path
        self.temp_dir.cleanup()

    def post_outcome(self, payload):
        request = urllib.request.Request(
            self.url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.token}",
            },
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=3) as response:
            return response.status, json.loads(response.read().decode("utf-8"))

    def test_outcome_is_saved_and_duplicate_does_not_train_twice(self):
        payload = {"deal_id": "deal-1001", "strategy_id": 12, "pnl": 5.25}
        status, first = self.post_outcome(payload)
        self.assertEqual(status, 200)
        self.assertTrue(first["accepted"])
        self.assertFalse(first["duplicate"])

        status, second = self.post_outcome(payload)
        self.assertEqual(status, 200)
        self.assertTrue(second["duplicate"])

        with service.connect_db() as db:
            count = db.execute(
                "SELECT COUNT(*) FROM outcomes WHERE client_id=? AND deal_id=?",
                (self.client_id, "deal-1001"),
            ).fetchone()[0]
            weight = db.execute(
                "SELECT weight FROM weights WHERE strategy_id='12'"
            ).fetchone()[0]
        self.assertEqual(count, 1)
        self.assertAlmostEqual(weight, 1.04)

    def test_non_finite_pnl_is_rejected(self):
        payload = {"deal_id": "deal-nan", "strategy_id": 12, "pnl": "NaN"}
        request = urllib.request.Request(
            self.url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.token}",
            },
            method="POST",
        )
        with self.assertRaises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(request, timeout=3)
        self.assertEqual(error.exception.code, 400)

    def test_same_broker_deal_id_is_isolated_between_clients(self):
        with service.connect_db() as db:
            db.execute(
                "INSERT INTO clients(client_id,token_hash,expires_at,revoked) VALUES(?,?,?,0)",
                ("second-client", hashlib.sha256(b"second-token").hexdigest(), time.time() + 3600),
            )
        self.post_outcome({"deal_id": "shared-ticket", "strategy_id": 12, "pnl": 2.0})

        second_url = f"http://127.0.0.1:{self.httpd.server_port}/v1/outcome/second-client"
        request = urllib.request.Request(
            second_url,
            data=json.dumps({"deal_id": "shared-ticket", "strategy_id": 12, "pnl": -1.0}).encode("utf-8"),
            headers={"Content-Type": "application/json", "Authorization": "Bearer second-token"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=3) as response:
            result = json.loads(response.read().decode("utf-8"))
        self.assertFalse(result["duplicate"])

        with service.connect_db() as db:
            count = db.execute(
                "SELECT COUNT(*) FROM outcomes WHERE deal_id='shared-ticket'"
            ).fetchone()[0]
        self.assertEqual(count, 2)


class OutcomeMigrationTests(unittest.TestCase):
    def test_legacy_outcomes_table_migrates_to_client_scoped_primary_key(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            old_db_path = service.DB_PATH
            service.DB_PATH = Path(temp_dir) / "legacy.sqlite3"
            try:
                with sqlite3.connect(service.DB_PATH) as db:
                    db.executescript("""
                    CREATE TABLE clients(
                        client_id TEXT PRIMARY KEY, token_hash TEXT NOT NULL,
                        expires_at REAL NOT NULL, revoked INTEGER NOT NULL DEFAULT 0
                    );
                    CREATE TABLE decisions(
                        decision_id TEXT PRIMARY KEY, client_id TEXT NOT NULL,
                        payload TEXT NOT NULL, created_at REAL NOT NULL
                    );
                    CREATE TABLE weights(strategy_id TEXT PRIMARY KEY, weight REAL NOT NULL);
                    CREATE TABLE outcomes(
                        deal_id TEXT PRIMARY KEY, strategy_id INTEGER NOT NULL,
                        pnl REAL NOT NULL, created_at REAL NOT NULL
                    );
                    INSERT INTO outcomes VALUES('old-ticket', 8, 3.5, 1700000000);
                    """)
                service.init_db()
                with service.connect_db() as db:
                    columns = list(db.execute("PRAGMA table_info(outcomes)"))
                    primary_key_columns = [
                        row["name"] for row in sorted(columns, key=lambda item: item["pk"]) if row["pk"]
                    ]
                    migrated = db.execute(
                        "SELECT client_id,deal_id,strategy_id,pnl FROM outcomes"
                    ).fetchone()
                self.assertEqual(primary_key_columns, ["client_id", "deal_id"])
                self.assertEqual(tuple(migrated), ("legacy", "old-ticket", 8, 3.5))
            finally:
                service.DB_PATH = old_db_path


if __name__ == "__main__":
    unittest.main()
