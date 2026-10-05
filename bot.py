import os, time, json, logging
from collections import deque
from pathlib import Path
import requests
import websocket

BASE = os.getenv("CAPITAL_BASE_URL", "https://demo-api-capital.backend-capital.com")
API_KEY = os.environ["CAPITAL_API_KEY"]
IDENTIFIER = os.getenv("CAPITAL_IDENTIFIER") or os.getenv("CAPITAL_EMAIL")
PASSWORD = os.environ["CAPITAL_PASSWORD"]
DRY_RUN = os.getenv("DRY_RUN", "false").lower() == "true"

EPIC = "GOLD"
LOTS = 0.01
MAX_POSITIONS = 20
RUN_SECONDS = int(os.getenv("RUN_SECONDS", "240"))

ENTRY_COOLDOWN = 0.60
MIN_REENTRY_MOVE = 0.03
PROFIT_TARGET = 0.10
PROFIT_LOCK = 0.05
TRAIL_GIVEBACK = 0.04
MAX_LOSS = 15.0

STATE_FILE = Path("bot_state.json")
WS_URL = "wss://api-streaming-capital.backend-capital.com/connect"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("gold_micro_scalper")


class Capital:
    def __init__(self):
        self.s = requests.Session()
        self.s.headers.update({"X-CAP-API-KEY": API_KEY, "Content-Type": "application/json"})

    def session(self):
        r = self.s.post(BASE + "/api/v1/session", json={
            "identifier": IDENTIFIER, "password": PASSWORD, "encryptedPassword": False
        }, timeout=20)
        if r.status_code >= 400:
            raise RuntimeError(f"session {r.status_code}: {r.text[:300]}")
        self.s.headers["CST"] = r.headers["CST"]
        self.s.headers["X-SECURITY-TOKEN"] = r.headers["X-SECURITY-TOKEN"]

    def get(self, path):
        r = self.s.get(BASE + path, timeout=15)
        if r.status_code >= 400:
            raise RuntimeError(f"GET {path} {r.status_code}: {r.text[:300]}")
        return r.json()

    def post(self, path, payload):
        r = self.s.post(BASE + path, json=payload, timeout=15)
        if r.status_code >= 400:
            raise RuntimeError(f"POST {path} {r.status_code}: {r.text[:300]}")
        return r.json()

    def delete(self, path):
        r = self.s.delete(BASE + path, timeout=15)
        if r.status_code >= 400:
            raise RuntimeError(f"DELETE {path} {r.status_code}: {r.text[:300]}")
        return r.json()

    def positions(self):
        return self.get("/api/v1/positions").get("positions", [])

    def markets(self):
        return self.get("/api/v1/markets").get("markets", [])

    def details(self):
        return self.get(f"/api/v1/markets/{EPIC}")

    def confirm(self, ref):
        return self.get(f"/api/v1/confirms/{ref}")

    def open(self, direction, size):
        if DRY_RUN:
            return None
        return self.post("/api/v1/positions", {
            "epic": EPIC, "direction": direction, "size": size, "guaranteedStop": False
        })

    def close(self, deal_id):
        if not DRY_RUN:
            return self.delete(f"/api/v1/positions/{deal_id}")


def load_state():
    try:
        x = json.loads(STATE_FILE.read_text())
        return x if isinstance(x, dict) and isinstance(x.get("owned"), dict) else {"owned": {}}
    except Exception:
        return {"owned": {}}


def save_state(state):
    STATE_FILE.write_text(json.dumps(state, indent=2) + "\n")


def trade_size(api):
    d = api.details()
    rules = d.get("dealingRules", {})
    market = d.get("market", d)
    lot_size = float(market.get("lotSize") or d.get("lotSize") or d.get("contractSize") or 100)
    minimum = float(rules.get("minDealSize", {}).get("value", 0) or 0)
    step = float(rules.get("minSizeIncrement", {}).get("value", 0) or 0)
    size = LOTS * lot_size
    if minimum and size < minimum:
        raise RuntimeError(f"GOLD 0.01 lot below minimum: {size} < {minimum}")
    if step and abs(round(size / step) * step - size) > 1e-9:
        raise RuntimeError(f"GOLD size {size} incompatible with step {step}")
    log.info("GOLD SIZE | lots=%.2f | lot_size=%.4f | api_size=%.4f | min=%.4f | step=%.4f",
             LOTS, lot_size, size, minimum, step)
    return size


def price_at(samples, seconds):
    target = samples[-1][0] - seconds
    for t, p in reversed(samples):
        if t <= target:
            return p
    return samples[0][1]


def signal(samples):
    if len(samples) < 12:
        return None, {}
    px = samples[-1][1]
    m05 = px - price_at(samples, .5)
    m1 = px - price_at(samples, 1)
    m2 = px - price_at(samples, 2)
    m4 = px - price_at(samples, 4)

    recent = [p for t, p in samples if t >= samples[-1][0] - 3]
    if len(recent) < 5:
        return None, {}

    hi, lo = max(recent), min(recent)
    rng = hi - lo
    if rng <= 0:
        return None, {}

    # Fast price-action / liquidity-sweep style entry.
    buy = (m05 > 0 and m1 > 0 and m2 > 0 and m1 >= 0.015 and m2 >= 0.025)
    sell = (m05 < 0 and m1 < 0 and m2 < 0 and m1 <= -0.015 and m2 <= -0.025)

    # Allow a fast reversal after a local sweep.
    sweep_buy = px > lo + rng * 0.35 and m05 > 0 and m1 > 0 and m4 > 0
    sweep_sell = px < hi - rng * 0.35 and m05 < 0 and m1 < 0 and m4 < 0

    info = {"m05": m05, "m1": m1, "m2": m2, "m4": m4}
    if buy or sweep_buy:
        return "BUY", info
    if sell or sweep_sell:
        return "SELL", info
    return None, info


def manage(api, item, state):
    p = item.get("position", {})
    deal_id = p.get("dealId")
    if not deal_id:
        return
    upl = float(p.get("upl", 0) or 0)
    e = state["owned"].setdefault(deal_id, {"peak": 0.0})
    e["peak"] = max(float(e.get("peak", 0) or 0), upl)

    if upl <= -MAX_LOSS:
        api.close(deal_id)
        log.warning("MAX LOSS CLOSE | %s | upl=%.2f", deal_id, upl)
        e["close_requested"] = True
        return

    if upl < PROFIT_TARGET:
        return

    # Small-profit scalping: close at +0.10.
    # Once profit is above +0.10, never allow it to fall below the locked floor.
    floor = max(PROFIT_LOCK, e["peak"] - TRAIL_GIVEBACK)
    if upl >= PROFIT_TARGET and upl <= floor:
        api.close(deal_id)
        log.info("PROFIT PROTECTION CLOSE | %s | upl=%.2f | peak=%.2f | floor=%.2f",
                 deal_id, upl, e["peak"], floor)
        e["close_requested"] = True
    elif upl >= PROFIT_TARGET:
        api.close(deal_id)
        log.info("MICRO PROFIT CLOSE | %s | upl=%.2f", deal_id, upl)
        e["close_requested"] = True


def open_confirmed(api, direction, size, state):
    before = {str(x.get("position", {}).get("dealId")) for x in api.positions()
              if x.get("position", {}).get("dealId")}
    result = api.open(direction, size)
    if DRY_RUN:
        log.info("DRY RUN ENTRY | GOLD | %s | %.4f", direction, size)
        return True

    ref = result.get("dealReference")
    if not ref:
        log.error("ORDER REJECTED | no dealReference | %s", result)
        return False

    for attempt in range(18):
        try:
            c = api.confirm(ref)
            status = str(c.get("dealStatus", "")).upper()
            affected = c.get("affectedDeals") or []
            reason = c.get("reason") or c.get("statusReason") or c.get("errorCode") or "NONE"
            log.info("OPEN CONFIRM | ref=%s | status=%s | affected=%d | attempt=%d/18",
                     ref, status, len(affected), attempt + 1)

            if status in {"REJECTED", "CANCELLED", "ERROR"}:
                log.error("GOLD ORDER REJECTED | %s | reason=%s", direction, reason)
                return False

            if affected:
                for d in affected:
                    deal_id = d.get("dealId") or d.get("dealReference")
                    if deal_id:
                        state["owned"][str(deal_id)] = {
                            "peak": 0.0, "direction": direction, "opened_at": time.time()
                        }
                        log.info("OWNED POSITION | %s | GOLD | %s", deal_id, direction)
                return True
        except Exception as e:
            log.warning("CONFIRM RETRY | %s | %s", ref, e)
        time.sleep(.35)

    # ACCEPTED with affectedDeals=0 is not treated as failure until live positions
    # are checked. Adopt any new GOLD position matching this order.
    try:
        for item in api.positions():
            p = item.get("position", {})
            m = item.get("market", {})
            did = p.get("dealId")
            live_epic = str(m.get("epic", "")).upper()
            live_dir = str(p.get("direction", "")).upper()
            live_size = float(p.get("size", 0) or 0)
            if did and str(did) not in before and live_epic == EPIC and live_dir == direction and abs(live_size-size) < max(.0001, size*.01):
                state["owned"][str(did)] = {"peak": 0.0, "direction": direction, "opened_at": time.time()}
                log.info("OWNED POSITION RECOVERED | %s | GOLD | %s | ref=%s", did, direction, ref)
                return True
    except Exception as e:
        log.warning("RECOVERY ERROR | %s", e)

    log.error("OPEN UNCONFIRMED | ref=%s | direction=%s | size=%.4f", ref, direction, size)
    return False


def run():
    api = Capital()
    api.session()

    markets = [m for m in api.markets()
               if str(m.get("epic", "")).upper() == EPIC and str(m.get("marketStatus", "")).upper() == "TRADEABLE"]
    if not markets:
        raise RuntimeError("GOLD is not tradeable")
    size = trade_size(api)

    state = load_state()
    # Remove any non-GOLD legacy ownership data.
    state["owned"] = {
        k: v for k, v in state["owned"].items()
        if str(v.get("epic", EPIC)).upper() == EPIC
    }
    samples = deque(maxlen=300)
    last_entry = 0.0
    last_px = None
    last_dir = None
    stop = time.time() + RUN_SECONDS

    log.info("GOLD MICRO SCALPER | DRY_RUN=%s | LOTS=0.01 | PROFIT=+0.10 AED | MAX_LOSS=-15 AED | MAX_POS=%d",
             DRY_RUN, MAX_POSITIONS)

    def on_quote(ts, px, bid, ask):
        nonlocal last_entry, last_px, last_dir
        if samples and ts <= samples[-1][0]:
            ts = samples[-1][0] + .0001
        if samples and abs(px - samples[-1][1]) < 1e-12:
            return
        samples.append((ts, px))

        try:
            positions = api.positions()
            gold = [x for x in positions if str(x.get("market", {}).get("epic", "")).upper() == EPIC]

            # Manage every live GOLD position, including positions opened before restart.
            for item in gold:
                did = item.get("position", {}).get("dealId")
                if did:
                    state["owned"].setdefault(str(did), {"peak": 0.0})
                    manage(api, item, state)

            positions = api.positions()
            gold = [x for x in positions if str(x.get("market", {}).get("epic", "")).upper() == EPIC]
            if len(gold) >= MAX_POSITIONS:
                return

            direction, info = signal(list(samples))
            if not direction:
                return

            now = time.time()
            if now - last_entry < ENTRY_COOLDOWN:
                return
            if last_dir == direction and last_px is not None and abs(px-last_px) < MIN_REENTRY_MOVE:
                return

            log.info("DIRECTION NORMAL | GOLD | strategy=%s | execution=%s | m1=%.5f m2=%.5f",
                     direction, direction, info.get("m1", 0), info.get("m2", 0))

            if open_confirmed(api, direction, size, state):
                last_entry, last_px, last_dir = now, px, direction
                log.info("ENTRY | GOLD | %s | size=%.4f=0.01lot | px=%.5f | positions=%d/%d",
                         direction, size, px, len(gold)+1, MAX_POSITIONS)
            save_state(state)
        except Exception as e:
            log.error("QUOTE CYCLE ERROR | %s", e)

    while time.time() < stop:
        ws = None
        try:
            ws = websocket.create_connection(WS_URL, timeout=3)
            ws.send(json.dumps({
                "destination": "marketData.subscribe",
                "correlationId": "gold-only",
                "cst": api.s.headers["CST"],
                "securityToken": api.s.headers["X-SECURITY-TOKEN"],
                "payload": {"epics": [EPIC]}
            }))
            log.info("GOLD WEBSOCKET CONNECTED | EPIC=GOLD")
            while time.time() < stop:
                try:
                    raw = ws.recv()
                except websocket.WebSocketTimeoutException:
                    continue
                if not raw:
                    continue
                try:
                    msg = json.loads(raw)
                except Exception:
                    continue
                if msg.get("destination") != "quote":
                    continue
                q = msg.get("payload", {})
                if str(q.get("epic", "")).upper() != EPIC:
                    continue
                bid, ask = q.get("bid"), q.get("ofr")
                if bid is None or ask is None:
                    continue
                bid, ask = float(bid), float(ask)
                if ask < bid:
                    continue
                ts = float(q.get("timestamp", time.time()*1000))/1000
                on_quote(ts, (bid+ask)/2, bid, ask)
        except Exception as e:
            log.warning("GOLD WEBSOCKET ERROR | %s", e)
            time.sleep(.7)
        finally:
            try:
                if ws: ws.close()
            except Exception: pass
        if time.time() < stop:
            try: api.session()
            except Exception as e: log.error("SESSION REFRESH | %s", e)

    save_state(state)
    log.info("GOLD RUN COMPLETE")


if __name__ == "__main__":
    run()
