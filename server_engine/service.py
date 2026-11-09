"""AL-ZOOL private central decision service. No MT5 credentials or terminal connection here."""
from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from contextlib import contextmanager
from pathlib import Path
import hashlib
import hmac
import json
import math
import os
import secrets
import sqlite3
import time
import uuid

from engine import evaluate_market

ROOT = Path(__file__).resolve().parent
DB_PATH = Path(os.environ.get("ALZOOL_DB_PATH", str(ROOT / "data" / "al_zool.sqlite3")))
HOST = os.environ.get("ALZOOL_BIND", "0.0.0.0")
PORT = int(os.environ.get("PORT", os.environ.get("ALZOOL_PORT", "8765")))
ADMIN_KEY = os.environ.get("ALZOOL_ADMIN_KEY", "")
MAX_BODY = 1_000_000
DECISION_TTL_SECONDS = 12


@contextmanager
def connect_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DB_PATH, timeout=20)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def init_db():
    with connect_db() as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS clients(
            client_id TEXT PRIMARY KEY, token_hash TEXT NOT NULL,
            expires_at REAL NOT NULL, revoked INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS decisions(
            decision_id TEXT PRIMARY KEY, client_id TEXT NOT NULL,
            payload TEXT NOT NULL, created_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS weights(
            strategy_id TEXT PRIMARY KEY, weight REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS outcomes(
            deal_id TEXT PRIMARY KEY, strategy_id INTEGER NOT NULL,
            pnl REAL NOT NULL, created_at REAL NOT NULL
        );
        """)
        # Migrate legacy global deal IDs to client-scoped IDs. A broker deal
        # ticket is only unique within an account, not across all customers.
        outcome_columns = {row["name"] for row in db.execute("PRAGMA table_info(outcomes)")}
        if "client_id" not in outcome_columns:
            db.executescript("""
            ALTER TABLE outcomes RENAME TO outcomes_legacy;
            CREATE TABLE outcomes(
                client_id TEXT NOT NULL, deal_id TEXT NOT NULL,
                strategy_id INTEGER NOT NULL, pnl REAL NOT NULL,
                created_at REAL NOT NULL,
                PRIMARY KEY(client_id, deal_id)
            );
            INSERT OR IGNORE INTO outcomes(client_id,deal_id,strategy_id,pnl,created_at)
                SELECT 'legacy',deal_id,strategy_id,pnl,created_at FROM outcomes_legacy;
            DROP TABLE outcomes_legacy;
            """)
        if db.execute("SELECT COUNT(*) FROM weights").fetchone()[0] == 0:
            db.executemany("INSERT INTO weights(strategy_id,weight) VALUES(?,1.0)",
                           [(str(i),) for i in range(1,201)])


def json_response(handler, status, value):
    raw = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(raw)))
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    handler.wfile.write(raw)


def signature_message(message: str, client_token: str) -> str:
    return hmac.new(client_token.encode("utf-8"), message.encode("utf-8"), hashlib.sha256).hexdigest()


def admin_authorized(handler) -> bool:
    supplied = handler.headers.get("X-Admin-Key", "")
    return bool(ADMIN_KEY) and hmac.compare_digest(supplied, ADMIN_KEY)


def authenticated_client(handler, client_id: str) -> bool:
    token = handler.headers.get("Authorization", "").removeprefix("Bearer ").strip()
    if not token:
        return False
    with connect_db() as db:
        row = db.execute("SELECT token_hash,expires_at,revoked FROM clients WHERE client_id=?",
                         (client_id,)).fetchone()
    if not row or row["revoked"] or row["expires_at"] <= time.time():
        return False
    digest = hashlib.sha256(token.encode()).hexdigest()
    return hmac.compare_digest(digest, row["token_hash"])


def validate_market(data):
    if not isinstance(data, dict) or data.get("symbol") != "XAUUSD":
        raise ValueError("symbol must be XAUUSD")
    candles = data.get("candles")
    if not isinstance(candles, list) or not 65 <= len(candles) <= 300:
        raise ValueError("candles must contain 65..300 closed bars")
    normalized = []
    previous_time = 0
    for item in candles:
        if not isinstance(item, dict):
            raise ValueError("invalid candle")
        candle = {}
        try:
            candle["time"] = int(item["time"])
            for key in ("open", "high", "low", "close"):
                candle[key] = float(item[key])
        except (KeyError, TypeError, ValueError, OverflowError):
            raise ValueError("invalid candle fields") from None
        prices = (candle["open"], candle["high"], candle["low"], candle["close"])
        if candle["time"] <= previous_time or candle["time"] <= 0:
            raise ValueError("candle times must be positive and strictly ascending")
        previous_time = candle["time"]
        if not all(math.isfinite(value) and 0 < value < 1e12 for value in prices):
            raise ValueError("invalid candle price")
        if candle["high"] < max(candle["open"], candle["close"], candle["low"]) or candle["low"] > min(candle["open"], candle["close"], candle["high"]):
            raise ValueError("inconsistent OHLC candle")
        if "tick_volume" in item:
            try:
                volume = int(item["tick_volume"])
            except (TypeError, ValueError, OverflowError):
                raise ValueError("invalid tick volume") from None
            if volume < 0:
                raise ValueError("invalid tick volume")
            candle["tick_volume"] = volume
        normalized.append(candle)
    try:
        bid, ask = float(data["bid"]), float(data["ask"])
    except (KeyError, TypeError, ValueError, OverflowError):
        raise ValueError("valid bid and ask are required") from None
    if not math.isfinite(bid) or not math.isfinite(ask) or not (0 < bid < 1e12 and 0 < ask < 1e12):
        raise ValueError("valid bid and ask are required")
    if ask < bid:
        raise ValueError("ask cannot be below bid")
    return normalized

class Handler(BaseHTTPRequestHandler):
    server_version = "ALZOOL-Central/1.0"

    def log_message(self, fmt, *args):
        print("%s - %s" % (self.client_address[0], fmt % args))

    def read_json(self):
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0 or length > MAX_BODY:
            raise ValueError("invalid request size")
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def do_GET(self):
        parts = self.path.strip("/").split("/")
        if self.path == "/health":
            return json_response(self, 200, {"service":"AL-ZOOL central engine","status":"online","trading_execution":False})
        if len(parts) == 3 and parts[:2] == ["v1","analysis"]:
            client_id = parts[2]
            if not authenticated_client(self, client_id):
                return json_response(self, 403, {"error":"client authorization failed"})
            with connect_db() as db:
                row = db.execute("SELECT payload,created_at FROM decisions WHERE client_id=? ORDER BY created_at DESC LIMIT 1",
                                 (client_id,)).fetchone()
            if not row:
                return json_response(self, 200, {"analysis":None})
            payload = json.loads(row["payload"])
            if time.time() - row["created_at"] > DECISION_TTL_SECONDS:
                return json_response(self, 200, {"analysis":None,"reason":"expired"})
            return json_response(self, 200, {"analysis":payload.get("analysis"),
                "decision_id":payload.get("decision_id"),"created_at":row["created_at"]})

        if len(parts) == 3 and parts[:2] == ["v1","decision"]:
            client_id = parts[2]
            if not authenticated_client(self, client_id):
                return json_response(self, 403, {"error":"client authorization failed"})
            with connect_db() as db:
                row = db.execute("SELECT payload,created_at FROM decisions WHERE client_id=? ORDER BY created_at DESC LIMIT 1",
                                 (client_id,)).fetchone()
            if not row:
                return json_response(self, 200, {"decision":None})
            payload = json.loads(row["payload"])
            if time.time() - row["created_at"] > DECISION_TTL_SECONDS:
                return json_response(self, 200, {"decision":None,"reason":"expired"})
            return json_response(self, 200, payload)
        return json_response(self, 404, {"error":"not found"})

    def do_POST(self):
        try:
            data = self.read_json()
        except Exception as exc:
            return json_response(self, 400, {"error":str(exc)})
        parts = self.path.strip("/").split("/")
        if self.path == "/admin/client":
            if not admin_authorized(self):
                return json_response(self, 401, {"error":"admin authorization required"})
            client_id = str(data.get("client_id","")).strip()
            days = int(data.get("days",0))
            if not client_id or len(client_id)>100 or not 1<=days<=3650:
                return json_response(self, 400, {"error":"client_id and days 1..3650 are required"})
            token = secrets.token_urlsafe(40)
            with connect_db() as db:
                db.execute("INSERT INTO clients(client_id,token_hash,expires_at,revoked) VALUES(?,?,?,0) "
                           "ON CONFLICT(client_id) DO UPDATE SET token_hash=excluded.token_hash,expires_at=excluded.expires_at,revoked=0",
                           (client_id,hashlib.sha256(token.encode()).hexdigest(),time.time()+days*86400))
            return json_response(self, 201, {"client_id":client_id,"client_token":token,"expires_in_days":days})
        if self.path == "/admin/revoke":
            if not admin_authorized(self):
                return json_response(self, 401, {"error":"admin authorization required"})
            client_id = str(data.get("client_id","")).strip()
            with connect_db() as db:
                cur = db.execute("UPDATE clients SET revoked=1 WHERE client_id=?",(client_id,))
            return json_response(self, 200 if cur.rowcount else 404, {"client_id":client_id,"revoked":bool(cur.rowcount)})
        if len(parts)==3 and parts[:2]==["v1","market"]:
            client_id = parts[2]
            if not authenticated_client(self, client_id):
                return json_response(self, 403, {"error":"client authorization failed"})
            try:
                rates = validate_market(data)
                mtf_bias = data.get("mtf_bias", {})
                if not isinstance(mtf_bias, dict) or any(
                    key not in ("M5", "M15", "H1") or value not in ("BUY", "SELL", "WAIT")
                    for key, value in mtf_bias.items()
                ):
                    raise ValueError("mtf_bias must contain only M5/M15/H1 BUY/SELL/WAIT values")
            except Exception as exc:
                return json_response(self, 400, {"error":str(exc)})
            with connect_db() as db:
                weights = {row["strategy_id"]:float(row["weight"]) for row in db.execute("SELECT strategy_id,weight FROM weights")}
            supplied_weights = data.get("weights")
            if supplied_weights is not None:
                if not isinstance(supplied_weights, dict) or len(supplied_weights) > 200:
                    return json_response(self, 400, {"error":"weights must be an object with at most 200 entries"})
                checked = {}
                try:
                    for key, value in supplied_weights.items():
                        sid = int(key)
                        weight = float(value)
                        if not 1 <= sid <= 200 or not math.isfinite(weight) or not 0.25 <= weight <= 4.0:
                            raise ValueError()
                        checked[str(sid)] = weight
                except (TypeError, ValueError, OverflowError):
                    return json_response(self, 400, {"error":"weights must contain strategy IDs 1..200 and values 0.25..4.0"})
                weights = checked
            mode = str(data.get("strategy_mode", "AUTO")).upper()
            if mode not in ("AUTO", "HUNTER", "SNIPER"):
                return json_response(self, 400, {"error":"strategy_mode must be AUTO, HUNTER or SNIPER"})
            result = evaluate_market(rates, weights=weights, mtf_bias=mtf_bias, mode=mode)
            created_at=time.time()
            expires_at=created_at+DECISION_TTL_SECONDS
            decision_id=str(uuid.uuid4())
            confidence=float(result.get("confidence",0.0))
            signed_payload=(f"{client_id}|{decision_id}|{result['decision']}|"
                            f"{int(result['strategy_id'])}|{confidence:.8f}|{expires_at:.6f}")
            client_token=self.headers.get("Authorization","").removeprefix("Bearer ").strip()
            payload = {"decision_id":decision_id,"client_id":client_id,
                       "decision":result["decision"],"strategy_id":result["strategy_id"],
                       "strategy_name":result["strategy_name"],"reason_ar":result["reason_ar"],
                       "confidence":confidence,"context":result["context"],"analysis":result,
                       "created_at":created_at,"expires_at":expires_at,
                       "signed_payload":signed_payload}
            payload["signature"] = signature_message(signed_payload,client_token)
            with connect_db() as db:
                db.execute("INSERT INTO decisions(decision_id,client_id,payload,created_at) VALUES(?,?,?,?)",
                           (payload["decision_id"],client_id,json.dumps(payload,ensure_ascii=False),payload["created_at"]))
            # Keep MT5 response compact; the desktop app fetches full analysis separately.
            mt5_payload = {key:value for key,value in payload.items() if key != "analysis"}
            return json_response(self, 200, mt5_payload)
        if len(parts)==3 and parts[:2]==["v1","outcome"]:
            client_id=parts[2]
            if not authenticated_client(self,client_id):
                return json_response(self,403,{"error":"client authorization failed"})
            deal_id=data.get("deal_id")
            strategy_raw=data.get("strategy_id")
            pnl_raw=data.get("pnl")
            if (not isinstance(deal_id,str) or not deal_id.strip() or len(deal_id)>120
                    or isinstance(strategy_raw,bool) or not isinstance(strategy_raw,int)
                    or isinstance(pnl_raw,bool)):
                return json_response(self,400,{"error":"valid deal_id, integer strategy_id and numeric pnl required"})
            try:
                pnl=float(pnl_raw)
            except (TypeError,ValueError,OverflowError):
                return json_response(self,400,{"error":"pnl must be a finite number"})
            strategy_id=strategy_raw
            if not 1<=strategy_id<=200 or not math.isfinite(pnl) or abs(pnl)>=1e9:
                return json_response(self,400,{"error":"strategy_id must be 1..200 and pnl must be finite"})
            deal_id=deal_id.strip()
            with connect_db() as db:
                cur=db.execute("INSERT OR IGNORE INTO outcomes(client_id,deal_id,strategy_id,pnl,created_at) VALUES(?,?,?,?,?)",
                               (client_id,deal_id,strategy_id,pnl,time.time()))
                if cur.rowcount:
                    row=db.execute("SELECT weight FROM weights WHERE strategy_id=?",(str(strategy_id),)).fetchone()
                    old=float(row["weight"]) if row else 1.0
                    new=max(.25,min(4.0,old*(1.04 if pnl>0 else .96 if pnl<0 else 1.0)))
                    db.execute("INSERT INTO weights(strategy_id,weight) VALUES(?,?) ON CONFLICT(strategy_id) DO UPDATE SET weight=excluded.weight",
                               (str(strategy_id),new))
            return json_response(self,200,{"accepted":True,"duplicate":cur.rowcount==0})
        return json_response(self,404,{"error":"not found"})


def main():
    if not ADMIN_KEY or len(ADMIN_KEY)<32:
        raise SystemExit("Set ALZOOL_ADMIN_KEY to a random secret of at least 32 characters.")
    init_db()
    print(f"AL-ZOOL central engine listening on {HOST}:{PORT}; MT5 execution stays on client devices.")
    ThreadingHTTPServer((HOST,PORT),Handler).serve_forever()


if __name__ == "__main__":
    main()
