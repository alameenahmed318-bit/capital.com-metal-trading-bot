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

# Fast, but not an uncontrolled order burst.
ENTRY_COOLDOWN = 1.50
MIN_REENTRY_MOVE = 0.05
MAX_SAME_DIRECTION = 3
ORDER_CONFIRM_TIMEOUT = 5.0

# Micro-scalp exit.
PROFIT_TARGET = 0.08
PROFIT_ARM = 0.01
TRAIL_GIVEBACK = 0.01
MAX_LOSS = 15.0

STATE_FILE = Path("bot_state.json")
WS_URL = "wss://api-streaming-capital.backend-capital.com/connect"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("gold_micro_scalper")


class Capital:
    def __init__(self):
        self.s = requests.Session()
        self.s.headers.update({
            "X-CAP-API-KEY": API_KEY,
            "Content-Type": "application/json",
        })

    def session(self):
        r = self.s.post(
            BASE + "/api/v1/session",
            json={"identifier": IDENTIFIER, "password": PASSWORD, "encryptedPassword": False},
            timeout=20,
        )
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
            "epic": EPIC,
            "direction": direction,
            "size": size,
            "guaranteedStop": False,
        })

    def close(self, deal_id):
        if DRY_RUN:
            return {}
        return self.delete(f"/api/v1/positions/{deal_id}")


def load_state():
    try:
        x = json.loads(STATE_FILE.read_text())
        if isinstance(x, dict) and isinstance(x.get("owned"), dict):
            return x
    except Exception:
        pass
    return {"owned": {}}


def save_state(state):
    STATE_FILE.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")


def gold_positions(api):
    return [
        x for x in api.positions()
        if str(x.get("market", {}).get("epic", "")).upper() == EPIC
    ]


def trade_size(api):
    d = api.details()
    rules = d.get("dealingRules", {})
    market = d.get("market", d)
    lot_size = float(
        market.get("lotSize")
        or d.get("lotSize")
        or d.get("contractSize")
        or 100
    )
    minimum = float(rules.get("minDealSize", {}).get("value", 0) or 0)
    step = float(rules.get("minSizeIncrement", {}).get("value", 0) or 0)
    size = LOTS

    if minimum and size < minimum:
        raise RuntimeError(f"GOLD 0.01 lot below minimum: {size} < {minimum}")
    if step:
        units = round(size / step)
        if abs(units * step - size) > 1e-9:
            raise RuntimeError(f"GOLD size {size} incompatible with step {step}")

    log.info(
        "GOLD SIZE | lots=%.2f | lot_size=%.4f | api_size=%.4f | min=%.4f | step=%.4f",
        LOTS, lot_size, size, minimum, step
    )
    return size


def price_at(samples, seconds):
    target = samples[-1][0] - seconds
    for t, p in reversed(samples):
        if t <= target:
            return p
    return samples[0][1]


def signal(samples):
    if len(samples) < 30:
        return None, {}

    prices = [p for _, p in samples]
    px = prices[-1]

    def ema(period):
        k = 2.0 / (period + 1.0)
        value = prices[-period]
        for p in prices[-period + 1:]:
            value = p * k + value * (1.0 - k)
        return value

    def rsi(period=7):
        window = prices[-(period + 1):]
        gains = []
        losses = []
        for x, y in zip(window, window[1:]):
            d = y - x
            gains.append(max(d, 0.0))
            losses.append(max(-d, 0.0))
        ag = sum(gains) / period
        al = sum(losses) / period
        if al <= 1e-12:
            return 100.0
        return 100.0 - (100.0 / (1.0 + ag / al))

    fast = ema(9)
    slow = ema(21)
    rv = rsi(7)
    m05 = px - prices[-16]
    m1 = px - prices[-30]
    recent = prices[-20:]
    hi, lo = max(recent), min(recent)
    rng = hi - lo

    # Fast Pulse: trend + RSI + immediate momentum.
    buy = fast > slow and rv >= 53 and m05 > 0 and m1 > 0
    sell = fast < slow and rv <= 47 and m05 < 0 and m1 < 0

    # Early breakout path keeps entries frequent when momentum is strong.
    breakout_buy = rng > 0 and px >= hi and m05 > 0
    breakout_sell = rng > 0 and px <= lo and m05 < 0

    info = {"ema9": fast, "ema21": slow, "rsi7": rv, "m05": m05, "m1": m1, "range": rng}
    if buy or breakout_buy:
        return "BUY", info
    if sell or breakout_sell:
        return "SELL", info
    return None, info

def close_confirmed(api, deal_id, reason, state):
    try:
        result = api.close(deal_id)
        ref = result.get("dealReference") if isinstance(result, dict) else None

        # Capital may return a deal reference for the close; confirm it.
        if ref:
            for attempt in range(8):
                try:
                    c = api.confirm(ref)
                    status = str(c.get("dealStatus", "")).upper()
                    affected = c.get("affectedDeals") or []
                    log.info(
                        "CLOSE CONFIRM | deal=%s | ref=%s | status=%s | affected=%d | attempt=%d/8",
                        deal_id, ref, status, len(affected), attempt
                    )
                    if status in {"REJECTED", "CANCELLED", "ERROR"}:
                        log.error("CLOSE REJECTED | deal=%s | reason=%s", deal_id, c)
                        return False
                    if status in {"ACCEPTED", "FILLED"} and not affected:
                        # Accepted close with no affected deals is normally completion.
                        break
                    if status in {"ACCEPTED", "FILLED"}:
                        break
                except Exception as e:
                    log.warning("CLOSE CONFIRM RETRY | deal=%s | %s", deal_id, e)
                time.sleep(0.25)

        state["owned"].pop(str(deal_id), None)
        log.info("POSITION CLOSE REQUESTED | GOLD | %s | reason=%s", deal_id, reason)
        return True
    except Exception as e:
        log.error("CLOSE ERROR | GOLD | deal=%s | reason=%s | %s", deal_id, reason, e)
        return False


def manage_positions(api, state):
    positions = gold_positions(api)

    for item in positions:
        p = item.get("position", {})
        deal_id = p.get("dealId")
        if not deal_id:
            continue

        deal_id = str(deal_id)
        upl = float(p.get("upl", 0) or 0)
        direction = str(p.get("direction", "UNKNOWN")).upper()
        entry = state["owned"].setdefault(
            deal_id,
            {"peak": 0.0, "direction": direction, "opened_at": time.time()},
        )
        entry["peak"] = max(float(entry.get("peak", 0) or 0), upl)

        if upl <= -MAX_LOSS:
            close_confirmed(api, deal_id, f"MAX_LOSS upl={upl:.2f}", state)
            continue

        peak = float(entry["peak"])

        # Protect immediately after a small profit appears.
        if peak >= PROFIT_ARM:
            floor = max(0.01, peak - TRAIL_GIVEBACK)
            if upl <= floor:
                close_confirmed(
                    api, deal_id,
                    f"QUICK_PROFIT_TRAIL upl={upl:.2f} peak={peak:.2f} floor={floor:.2f}",
                    state,
                )
                continue

        # Small scalp target; no fixed large TP.
        if upl >= PROFIT_TARGET:
            close_confirmed(api, deal_id, f"QUICK_TAKE_PROFIT upl={upl:.2f}", state)
            continue

        log.info(
            "POSITION MONITOR | GOLD | %s | dir=%s | upl=%+.2f | peak=%+.2f",
            deal_id, direction, upl, peak
        )

def open_confirmed(api, direction, size, state):
    try:
        before = {
            str(x.get("position", {}).get("dealId"))
            for x in gold_positions(api)
            if x.get("position", {}).get("dealId")
        }

        result = api.open(direction, size)
        if DRY_RUN:
            log.info("DRY RUN ENTRY | GOLD | %s | %.4f", direction, size)
            return True

        ref = result.get("dealReference")
        if not ref:
            log.error("ORDER REJECTED | GOLD | no dealReference | %s", result)
            return False

        confirm_deadline = time.time() + ORDER_CONFIRM_TIMEOUT
        attempt = 0
        while time.time() < confirm_deadline:
            attempt += 1
            c = api.confirm(ref)
            status = str(c.get("dealStatus", "")).upper()
            affected = c.get("affectedDeals") or []
            reason = c.get("reason") or c.get("statusReason") or c.get("errorCode") or "NONE"

            log.info(
                "OPEN CONFIRM | ref=%s | status=%s | affected=%d | attempt=%d/12",
                ref, status, len(affected), attempt + 1
            )

            if status in {"REJECTED", "CANCELLED", "ERROR"}:
                log.error("GOLD ORDER REJECTED | %s | reason=%s | raw=%s", direction, reason, c)
                return False

            if affected:
                for d in affected:
                    did = d.get("dealId") or d.get("dealReference")
                    if did:
                        state["owned"][str(did)] = {
                            "peak": 0.0,
                            "direction": direction,
                            "opened_at": time.time(),
                        }
                        log.info("OWNED POSITION | %s | GOLD | %s", did, direction)
                return True

            time.sleep(0.25)

        # Recover an accepted order that did not expose affectedDeals immediately.
        for item in gold_positions(api):
            p = item.get("position", {})
            did = p.get("dealId")
            live_dir = str(p.get("direction", "")).upper()
            live_size = float(p.get("size", 0) or 0)
            if (
                did
                and str(did) not in before
                and live_dir == direction
                and abs(live_size - size) < max(0.0001, size * 0.01)
            ):
                state["owned"][str(did)] = {
                    "peak": 0.0,
                    "direction": direction,
                    "opened_at": time.time(),
                }
                log.info("OWNED POSITION RECOVERED | %s | GOLD | %s | ref=%s", did, direction, ref)
                return True

        log.error("OPEN UNCONFIRMED | GOLD | ref=%s | direction=%s | size=%.4f", ref, direction, size)
        return False

    except Exception as e:
        log.error("OPEN ERROR | GOLD | %s | %s", direction, e)
        return False


def run():
    api = Capital()
    api.session()

    tradeable = [
        m for m in api.markets()
        if str(m.get("epic", "")).upper() == EPIC
        and str(m.get("marketStatus", "")).upper() == "TRADEABLE"
    ]
    if not tradeable:
        raise RuntimeError("GOLD is not tradeable")

    size = trade_size(api)
    state = load_state()

    # Keep state GOLD-only. Existing live GOLD positions are adopted and managed.
    state["owned"] = {
        k: v for k, v in state["owned"].items()
        if str(v.get("epic", EPIC)).upper() == EPIC
    }

    samples = deque(maxlen=300)
    last_entry = 0.0
    last_px = None
    last_dir = None
    last_monitor = 0.0
    stop = time.time() + RUN_SECONDS

    log.info(
        "GOLD QUICK PULSE | DRY_RUN=%s | EPIC=GOLD | LOTS=0.01 | TARGET=+0.08 AED | PROFIT_ARM=+0.01 AED | TRAIL=0.01 AED | MAX_LOSS=-15 AED | MAX_POS=%d | MAX_SAME_DIRECTION=%d",
        DRY_RUN, MAX_POSITIONS
    )

    def on_quote(ts, px):
        nonlocal last_entry, last_px, last_dir, last_monitor

        if samples and ts <= samples[-1][0]:
            ts = samples[-1][0] + 0.0001
        if samples and abs(px - samples[-1][1]) < 1e-12:
            return
        samples.append((ts, px))

        now = time.time()

        try:
            # Position management runs independently of entry decisions.
            # This fixes the previous issue where exits could be missed.
            if now - last_monitor >= 0.15:
                manage_positions(api, state)
                save_state(state)
                last_monitor = now

            positions = gold_positions(api)
            if len(positions) >= MAX_POSITIONS:
                return

            direction, info = signal(list(samples))
            if not direction:
                return

            if now - last_entry < ENTRY_COOLDOWN:
                return

            if last_dir == direction and last_px is not None and abs(px - last_px) < MIN_REENTRY_MOVE:
                return

            same_direction = sum(1 for x in positions if str(x.get("position", {}).get("direction", "")).upper() == direction)
            if same_direction >= MAX_SAME_DIRECTION:
                log.info("ENTRY BLOCKED | GOLD | direction=%s | same_direction=%d/%d", direction, same_direction, MAX_SAME_DIRECTION)
                return

            log.info(
                "DIRECTION NORMAL | GOLD | strategy=%s | execution=%s | m1=%.5f m2=%.5f",
                direction, direction, info.get("m1", 0), info.get("m2", 0)
            )

            if open_confirmed(api, direction, size, state):
                last_entry = now
                last_px = px
                last_dir = direction
                log.info(
                    "ENTRY | GOLD | %s | size=%.4f=0.01 | px=%.5f | positions_before=%d/%d",
                    direction, size, px, len(positions), MAX_POSITIONS
                )
                save_state(state)

        except Exception as e:
            log.error("QUOTE CYCLE ERROR | GOLD | %s", e)

    while time.time() < stop:
        ws = None
        try:
            ws = websocket.create_connection(WS_URL, timeout=3)
            ws.send(json.dumps({
                "destination": "marketData.subscribe",
                "correlationId": "gold-only",
                "cst": api.s.headers["CST"],
                "securityToken": api.s.headers["X-SECURITY-TOKEN"],
                "payload": {"epics": [EPIC]},
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

                ts = float(q.get("timestamp", time.time() * 1000)) / 1000
                on_quote(ts, (bid + ask) / 2)

        except Exception as e:
            log.warning("GOLD WEBSOCKET ERROR | %s", e)
            time.sleep(0.7)

        finally:
            try:
                if ws:
                    ws.close()
            except Exception:
                pass

        if time.time() < stop:
            try:
                api.session()
            except Exception as e:
                log.error("SESSION REFRESH | %s", e)

    save_state(state)
    log.info("GOLD RUN COMPLETE")


if __name__ == "__main__":
    run()
