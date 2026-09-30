import os, time, json, logging
from pathlib import Path
import requests

BASE = os.getenv("CAPITAL_BASE_URL", "https://demo-api-capital.backend-capital.com")
API_KEY = os.environ["CAPITAL_API_KEY"]
IDENTIFIER = os.environ["CAPITAL_IDENTIFIER"]
PASSWORD = os.environ["CAPITAL_PASSWORD"]

SIZE = float(os.getenv("TRADE_SIZE", "0.01"))
DRY_RUN = os.getenv("DRY_RUN", "true").lower() == "true"
SCAN_SECONDS = 2
RUN_SECONDS = int(os.getenv("RUN_SECONDS", "720"))
HISTORY_REFRESH_SECONDS = int(os.getenv("HISTORY_REFRESH_SECONDS", "60"))
MAX_INITIAL_LOSS = float(os.getenv("MAX_INITIAL_LOSS_AED", "10"))
PROFIT_ARM = float(os.getenv("PROFIT_ARM_AED", "1"))
PROFIT_LOCK = float(os.getenv("PROFIT_LOCK_AED", "0.25"))
TRAIL_GIVEBACK = float(os.getenv("TRAIL_GIVEBACK_AED", "1.0"))
STATE_FILE = Path("bot_state.json")

log = logging.getLogger("hybrid")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


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
        r.raise_for_status()
        self.s.headers.update({
            "CST": r.headers.get("CST"),
            "X-SECURITY-TOKEN": r.headers.get("X-SECURITY-TOKEN"),
        })

    def get(self, path, **params):
        r = self.s.get(BASE + path, params=params, timeout=20)
        r.raise_for_status()
        return r.json()

    def post(self, path, payload):
        r = self.s.post(BASE + path, json=payload, timeout=20)
        r.raise_for_status()
        return r.json()

    def put(self, path, payload):
        r = self.s.put(BASE + path, json=payload, timeout=20)
        r.raise_for_status()
        return r.json()

    def positions(self):
        return self.get("/api/v1/positions").get("positions", [])

    def markets(self):
        return self.get("/api/v1/markets").get("markets", [])

    def prices(self, epic, n=100):
        return self.get(f"/api/v1/prices/{epic}", resolution="MINUTE", max=n)

    def confirm(self, deal_reference):
        return self.get(f"/api/v1/confirms/{deal_reference}")

    def open(self, epic, direction, size):
        if DRY_RUN:
            log.info("DRY RUN | OPEN %s %s %.4f", direction, epic, size)
            return None
        return self.post("/api/v1/positions", {
            "epic": epic,
            "direction": direction,
            "size": size,
            "guaranteedStop": False,
            "stopAmount": MAX_INITIAL_LOSS,
        })

    def update_position(self, deal_id, stop_level):
        return self.put(
            f"/api/v1/positions/{deal_id}",
            {"guaranteedStop": False, "stopLevel": float(stop_level)},
        )


def load_state():
    if not STATE_FILE.exists():
        return {"owned": {}}
    try:
        data = json.loads(STATE_FILE.read_text())
        if not isinstance(data, dict):
            return {"owned": {}}
        data.setdefault("owned", {})
        return data
    except Exception:
        return {"owned": {}}


def save_state(state):
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True))
    tmp.replace(STATE_FILE)


def mid_from_quote(market):
    bid, ask = market.get("bid"), market.get("offer")
    if bid is None or ask is None:
        return None
    return (float(bid) + float(ask)) / 2.0


def mid_price(p):
    cp = p.get("closePrice", {})
    bid, ask = cp.get("bid"), cp.get("ask")
    if bid is None or ask is None:
        return None
    return (float(bid) + float(ask)) / 2.0


def candles(raw):
    out = []
    for x in raw.get("prices", []):
        m = mid_price(x)
        if m is not None:
            out.append(m)
    return out


def ema(xs, n):
    if len(xs) < n:
        return None
    k = 2 / (n + 1)
    e = sum(xs[:n]) / n
    for v in xs[n:]:
        e = v * k + e * (1 - k)
    return e


def atr_like(xs, n=14):
    if len(xs) < n + 1:
        return None
    return sum(abs(xs[i] - xs[i - 1]) for i in range(len(xs) - n, len(xs))) / n


def signal(xs):
    if len(xs) < 40:
        return None
    e9, e21 = ema(xs, 9), ema(xs, 21)
    p9, p21 = ema(xs[:-1], 9), ema(xs[:-1], 21)
    vol = atr_like(xs)
    if not all(v is not None for v in (e9, e21, p9, p21, vol)) or vol <= 0:
        return None

    momentum = (xs[-1] - xs[-6]) / vol
    cross_up = p9 <= p21 and e9 > e21
    cross_dn = p9 >= p21 and e9 < e21
    trend_up = e9 > e21 and momentum > 0.25
    trend_dn = e9 < e21 and momentum < -0.25

    if cross_up or (trend_up and momentum > 0.6):
        return "BUY"
    if cross_dn or (trend_dn and momentum < -0.6):
        return "SELL"
    return None


def forex_markets(all_markets):
    out = []
    for m in all_markets:
        if str(m.get("instrumentType", "")).upper() != "CURRENCIES":
            continue
        if str(m.get("marketStatus", "")).upper() != "TRADEABLE":
            continue
        epic = m.get("epic")
        if epic:
            out.append(m)
    return sorted(out, key=lambda x: x["epic"])


def owned_open_positions(positions, state):
    owned = {}
    open_ids = set()

    for item in positions:
        p = item.get("position", {})
        deal_id = p.get("dealId")
        if not deal_id:
            continue
        if deal_id in state["owned"]:
            owned[deal_id] = item
            open_ids.add(deal_id)

    for deal_id in list(state["owned"]):
        if deal_id not in open_ids:
            del state["owned"][deal_id]

    return owned


def current_price(market):
    return mid_from_quote(market)


def value_per_price(position, current):
    p = position.get("position", {})
    entry = float(p.get("level", 0) or 0)
    upl = float(p.get("upl", 0) or 0)
    move = abs(current - entry)
    if move <= 0 or abs(upl) <= 0:
        return None
    return abs(upl) / move


def tighten_profit_stop(api, item, state_entry, market):
    p = item.get("position", {})
    deal_id = p.get("dealId")
    direction = str(p.get("direction", "")).upper()
    upl = float(p.get("upl", 0) or 0)
    entry = float(p.get("level", 0) or 0)
    px = current_price(market)

    if not deal_id or direction not in ("BUY", "SELL") or px is None or entry <= 0:
        return

    peak = max(float(state_entry.get("peak_upl", 0)), upl)
    state_entry["peak_upl"] = peak

    # Never turn a profitable position back into a losing one.
    if peak < PROFIT_ARM:
        return

    vpp = value_per_price(p, px)
    if not vpp:
        return

    if peak < PROFIT_ARM + TRAIL_GIVEBACK:
        lock_price = PROFIT_LOCK / vpp
        target = entry + lock_price if direction == "BUY" else entry - lock_price
    else:
        giveback_price = TRAIL_GIVEBACK / vpp
        target = px - giveback_price if direction == "BUY" else px + giveback_price

    old = state_entry.get("stop_level")
    if old is not None:
        old = float(old)

    improve = (
        old is None
        or (direction == "BUY" and target > old)
        or (direction == "SELL" and target < old)
    )

    if not improve:
        return

    if DRY_RUN:
        log.info(
            "DRY RUN | PROFIT PROTECT | %s | UPL=%.2f | stop %.8f -> %.8f",
            deal_id, upl, old if old is not None else 0.0, target
        )
        state_entry["stop_level"] = target
        return

    try:
        api.update_position(deal_id, target)
        state_entry["stop_level"] = target
        log.info(
            "PROFIT PROTECT | %s | UPL=%.2f | stop -> %.8f",
            deal_id, upl, target
        )
    except Exception as e:
        log.warning("profit stop update failed %s: %s", deal_id, e)


def open_bot_position(api, epic, direction, size, state, market):
    result = api.open(epic, direction, size)
    if DRY_RUN or not result:
        return

    deal_ref = result.get("dealReference")
    if not deal_ref:
        log.warning("OPEN %s returned no dealReference", epic)
        return

    # Capital.com requires confirmation to obtain the permanent dealId.
    confirmed = None
    for _ in range(5):
        try:
            confirmed = api.confirm(deal_ref)
            if confirmed and confirmed.get("affectedDeals"):
                break
        except Exception:
            pass
        time.sleep(0.4)

    affected = (confirmed or {}).get("affectedDeals", [])
    for deal in affected:
        deal_id = deal.get("dealId") or deal.get("dealReference")
        if deal_id:
            state["owned"][deal_id] = {
                "epic": epic,
                "direction": direction,
                "peak_upl": 0.0,
                "stop_level": None,
            }
            log.info("OWNED POSITION | %s | %s", deal_id, epic)


def refresh_history(api, epic, cache):
    now = time.time()
    row = cache.get(epic)
    if row and now - row.get("ts", 0) < HISTORY_REFRESH_SECONDS:
        return row.get("xs", [])
    raw = api.prices(epic, 100)
    xs = candles(raw)
    if xs:
        cache[epic] = {"ts": now, "xs": xs[-120:]}
    return cache.get(epic, {}).get("xs", [])


def refresh_history_budget(api, epics, cache, budget=2):
    refreshed = 0
    for epic in epics:
        if refreshed >= budget:
            break
        row = cache.get(epic)
        if row and time.time() - row.get("ts", 0) < HISTORY_REFRESH_SECONDS:
            continue
        try:
            refresh_history(api, epic, cache)
            refreshed += 1
            time.sleep(0.12)
        except Exception as e:
            log.warning("%s history refresh failed: %s", epic, e)
    return refreshed


def run():
    api = Capital()
    api.session()
    state = load_state()
    history = {}
    cycle_started = time.time()

    log.info(
        "HYBRID FX BOT | DRY_RUN=%s | SCAN=2s | ALL TRADEABLE CURRENCIES ONLY",
        DRY_RUN,
    )

    while True:
        try:
            # One market snapshot covers all currency instruments and avoids
            # hammering the REST API once per pair every 2 seconds.
            markets = forex_markets(api.markets())
            market_by_epic = {m["epic"]: m for m in markets}

            positions = api.positions()
            owned = owned_open_positions(positions, state)

            refresh_history_budget(api, list(market_by_epic), history, budget=2)

            # Profit protection runs first, every 2 seconds.
            for deal_id, item in owned.items():
                epic = item.get("market", {}).get("epic")
                if epic in market_by_epic:
                    tighten_profit_stop(api, item, state["owned"][deal_id], market_by_epic[epic])

            # Entries are restricted to currencies. Existing positions on the
            # same epic block a new entry, including manual/other-bot positions.
            for epic, market in market_by_epic.items():
                try:
                    xs = refresh_history(api, epic, history)
                    if len(xs) < 40:
                        continue

                    px = current_price(market)
                    if px is None:
                        continue

                    # Inject the current quote into the minute history for a
                    # fresh decision without requesting history every 2 sec.
                    working = (xs + [px])[-120:]
                    sig = signal(working)

                    existing_any = [
                        x for x in positions
                        if x.get("market", {}).get("epic") == epic
                    ]

                    if sig and not existing_any:
                        open_bot_position(api, epic, sig, SIZE, state, market)
                        log.info("ENTRY | %s -> %s", epic, sig)
                except Exception as e:
                    log.warning("%s scan failed: %s", epic, e)

            save_state(state)
            if time.time() - cycle_started >= RUN_SECONDS:
                log.info("RUN COMPLETE | elapsed=%ds", int(time.time() - cycle_started))
                break
            time.sleep(SCAN_SECONDS)

        except Exception as e:
            log.error("cycle error: %s", e)
            save_state(state)
            time.sleep(5)
            try:
                api.session()
            except Exception:
                pass


if __name__ == "__main__":
    run()
