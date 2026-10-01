import os, time, json, logging
from pathlib import Path
import requests

BASE = os.getenv("CAPITAL_BASE_URL", "https://demo-api-capital.backend-capital.com")
API_KEY = os.environ["CAPITAL_API_KEY"]
IDENTIFIER = os.environ.get("CAPITAL_IDENTIFIER") or os.environ["CAPITAL_EMAIL"]
PASSWORD = os.environ["CAPITAL_PASSWORD"]

SIZE = float(os.getenv("TRADE_SIZE", "0.01"))
DRY_RUN = os.getenv("DRY_RUN", "true").lower() == "true"
SCAN_SECONDS = 2
MARKET_REFRESH_SECONDS = 10
POSITION_REFRESH_SECONDS = 5
PRICE_RESOLUTION = "MINUTE_5"
TREND_RESOLUTION = "MINUTE_15"
RUN_SECONDS = int(os.getenv("RUN_SECONDS", "720"))
HISTORY_REFRESH_SECONDS = int(os.getenv("HISTORY_REFRESH_SECONDS", "60"))
MAX_INITIAL_LOSS = float(os.getenv("MAX_INITIAL_LOSS_AED", "10"))
PROFIT_ARM = float(os.getenv("PROFIT_ARM_AED", "1"))
PROFIT_LOCK = float(os.getenv("PROFIT_LOCK_AED", "0.25"))
TRAIL_GIVEBACK = float(os.getenv("TRAIL_GIVEBACK_AED", "1.0"))
PROFIT_TRAIL_RATIO = float(os.getenv("PROFIT_TRAIL_RATIO", "0.30"))
STRONG_RETRACE_RATIO = float(os.getenv("STRONG_RETRACE_RATIO", "0.35"))
ORDER_COOLDOWN_SECONDS = int(os.getenv("ORDER_COOLDOWN_SECONDS", "60"))
MARKET_RULES_REFRESH_SECONDS = int(os.getenv("MARKET_RULES_REFRESH_SECONDS", "3600"))
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
        if r.status_code >= 400:
            detail = r.text[:500].replace("\n", " ")
            raise RuntimeError(f"Capital session failed ({r.status_code}): {detail}")
        self.s.headers.update({
            "CST": r.headers.get("CST"),
            "X-SECURITY-TOKEN": r.headers.get("X-SECURITY-TOKEN"),
        })

    def get(self, path, **params):
        r = self.s.get(BASE + path, params=params, timeout=20)
        if r.status_code >= 400:
            detail = r.text[:500].replace("\n", " ")
            raise RuntimeError(f"Capital GET {path} failed ({r.status_code}): {detail}")
        return r.json()
    def post(self, path, payload):
        r = self.s.post(BASE + path, json=payload, timeout=20)
        if r.status_code >= 400:
            detail = r.text[:500].replace("\\n", " ")
            raise RuntimeError(f"Capital POST {path} failed ({r.status_code}): {detail}")
        return r.json()

    def put(self, path, payload):
        r = self.s.put(BASE + path, json=payload, timeout=20)
        if r.status_code >= 400:
            detail = r.text[:500].replace("\n", " ")
            raise RuntimeError(f"Capital PUT {path} failed ({r.status_code}): {detail}")
        return r.json()

    def delete(self, path):
        r = self.s.delete(BASE + path, timeout=20)
        if r.status_code >= 400:
            detail = r.text[:500].replace("\n", " ")
            raise RuntimeError(f"Capital DELETE {path} failed ({r.status_code}): {detail}")
        return r.json()

    def positions(self):
        return self.get("/api/v1/positions").get("positions", [])

    def markets(self):
        return self.get("/api/v1/markets").get("markets", [])

    def market_details(self, epic):
        return self.get(f"/api/v1/markets/{epic}")

    def prices(self, epic, n=100, resolution=PRICE_RESOLUTION):
        return self.get(f"/api/v1/prices/{epic}", resolution=resolution, max=n)

    def confirm(self, deal_reference):
        return self.get(f"/api/v1/confirms/{deal_reference}")

    def open(self, epic, direction, size):
        if DRY_RUN:
            log.info("DRY RUN | OPEN %s %s %.4f", direction, epic, size)
            return None
        # Capital can reject a universal stopAmount because the valid
        # stop-loss range is instrument/price/size dependent. Open first,
        # then enforce the account-currency loss cap locally from live UPL.
        return self.post("/api/v1/positions", {
            "epic": epic,
            "direction": direction,
            "size": size,
            "guaranteedStop": False,
        })

    def update_position(self, deal_id, stop_level):
        return self.put(
            f"/api/v1/positions/{deal_id}",
            {"guaranteedStop": False, "stopLevel": float(stop_level)},
        )

    def close_position(self, deal_id):
        return self.delete(f"/api/v1/positions/{deal_id}")


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


def signal(m5, m15):
    # Entry uses current M5 price action with M15 directional context.
    # No fixed score threshold.
    if len(m5) < 16 or len(m15) < 16:
        return None

    m5_fast, m5_slow = ema(m5, 5), ema(m5, 13)
    p5_fast, p5_slow = ema(m5[:-1], 5), ema(m5[:-1], 13)
    m15_fast, m15_slow = ema(m15, 5), ema(m15, 13)
    if not all(v is not None for v in (m5_fast, m5_slow, p5_fast, p5_slow, m15_fast, m15_slow)):
        return None

    recent = m5[-4:]
    move = recent[-1] - recent[0]
    range_ref = max(max(recent) - min(recent), abs(move), 1e-12)
    cross_up = p5_fast <= p5_slow and m5_fast > m5_slow
    cross_dn = p5_fast >= p5_slow and m5_fast < m5_slow
    momentum_up = move > 0 and move / range_ref >= 0.20
    momentum_dn = move < 0 and abs(move) / range_ref >= 0.20
    trend_up = m15_fast >= m15_slow
    trend_dn = m15_fast <= m15_slow

    if trend_up and (cross_up or momentum_up):
        return "BUY"
    if trend_dn and (cross_dn or momentum_dn):
        return "SELL"
    return None

def tradable_markets(all_markets):
    out = []
    for m in all_markets:
        if str(m.get("marketStatus", "")).upper() != "TRADEABLE":
            continue
        instrument_type = str(m.get("instrumentType", "")).upper()
        epic = str(m.get("epic", "")).upper()
        name = str(m.get("instrumentName", m.get("name", ""))).upper()
        is_fx = instrument_type == "CURRENCIES"
        requested = (
            epic in {"GOLD", "SILVER", "US500", "US100", "US1000"}
            or "GOLD" in name
            or "SILVER" in name
            or "US TECH 100" in name
            or "TECH 100" in name
            or "S&P 500" in name
            or "US 500" in name
        )
        if is_fx or requested:
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
    """
    Monotonic profit protection:
    As soon as the position is profitable, protect it at/above break-even.
    After protection is armed, the broker stop only moves toward more profit.
    """
    p = item.get("position", {})
    deal_id = p.get("dealId")
    direction = str(p.get("direction", "")).upper()
    upl = float(p.get("upl", 0) or 0)
    entry = float(p.get("level", 0) or 0)
    px = current_price(market)

    if not deal_id or direction not in ("BUY", "SELL") or px is None or entry <= 0:
        return

    # Hard account-currency loss cap. This replaces the rejected universal
    # broker stopAmount and only closes once the live loss reaches/exceeds
    # the configured cap. It never closes a losing trade before this level.
    if upl <= -MAX_INITIAL_LOSS:
        if DRY_RUN:
            log.info("DRY RUN | MAX LOSS CLOSE | %s | UPL=%.2f | limit=-%.2f",
                     deal_id, upl, MAX_INITIAL_LOSS)
        else:
            try:
                api.close_position(deal_id)
                log.warning("MAX LOSS CLOSE | %s | UPL=%.2f | limit=-%.2f",
                            deal_id, upl, MAX_INITIAL_LOSS)
                state_entry["stop_level"] = None
            except Exception as e:
                log.warning("max loss close failed %s: %s", deal_id, e)
        return

    peak = max(float(state_entry.get("peak_upl", 0) or 0), upl)
    state_entry["peak_upl"] = peak

    vpp = value_per_price(p, px)
    if not vpp:
        return

    # Protect immediately once the live UPL turns positive.
    if upl > 0:
        initial_lock = min(PROFIT_LOCK, max(0.01, upl * 0.50))
        target = entry + initial_lock / vpp if direction == "BUY" else entry - initial_lock / vpp

        old = state_entry.get("stop_level")
        old = float(old) if old is not None else None
        improve = (
            old is None
            or (direction == "BUY" and target > old)
            or (direction == "SELL" and target < old)
        )
        if improve:
            if DRY_RUN:
                log.info("DRY RUN | BREAK-EVEN PROTECT | %s | UPL=%.2f | lock=%.2f | stop -> %.8f",
                         deal_id, upl, initial_lock, target)
                state_entry["stop_level"] = target
            else:
                try:
                    api.update_position(deal_id, target)
                    state_entry["stop_level"] = target
                    log.info("BREAK-EVEN PROTECT | %s | UPL=%.2f | lock=%.2f | stop -> %.8f",
                             deal_id, upl, initial_lock, target)
                except Exception as e:
                    log.warning("break-even protection update failed %s: %s", deal_id, e)

    # Once the trade reaches the normal profit-arm level, lock a portion
    # of the peak profit. The stop is strictly monotonic.
    if peak < PROFIT_ARM:
        return

    giveback = max(TRAIL_GIVEBACK, peak * PROFIT_TRAIL_RATIO)
    # A strong retracement is allowed to close the trade directly while it
    # is still profitable. This is the fallback for cases where the broker
    # rejects a stop modification or the price gaps through the stop.
    retracement = peak - upl
    locked_profit = max(PROFIT_LOCK, peak - giveback)
    if locked_profit <= 0:
        return

    if upl >= PROFIT_LOCK and retracement >= giveback and upl <= locked_profit:
        if DRY_RUN:
            log.info("DRY RUN | STRONG RETRACE CLOSE | %s | UPL=%.2f | PEAK=%.2f | locked=%.2f",
                     deal_id, upl, peak, locked_profit)
        else:
            try:
                api.close_position(deal_id)
                log.info("STRONG RETRACE CLOSE | %s | UPL=%.2f | PEAK=%.2f | locked=%.2f",
                         deal_id, upl, peak, locked_profit)
                state_entry["stop_level"] = None
            except Exception as e:
                log.warning("strong retrace close failed %s: %s", deal_id, e)
        return

    target = entry + locked_profit / vpp if direction == "BUY" else entry - locked_profit / vpp
    old = state_entry.get("stop_level")
    old = float(old) if old is not None else None

    improve = (
        old is None
        or (direction == "BUY" and target > old)
        or (direction == "SELL" and target < old)
    )
    if not improve:
        return

    if DRY_RUN:
        log.info("DRY RUN | PROFIT LOCK | %s | UPL=%.2f | PEAK=%.2f | locked=%.2f | stop -> %.8f",
                 deal_id, upl, peak, locked_profit, target)
        state_entry["stop_level"] = target
        return

    try:
        api.update_position(deal_id, target)
        state_entry["stop_level"] = target
        log.info("PROFIT LOCK | %s | UPL=%.2f | PEAK=%.2f | locked=%.2f | stop -> %.8f",
                 deal_id, upl, peak, locked_profit, target)
    except Exception as e:
        log.warning("profit lock update failed %s: %s", deal_id, e)


def normalize_size(api, epic, configured_size, rules_cache):
    now = time.time()
    row = rules_cache.get(epic)
    if row and now - row.get("ts", 0) < MARKET_RULES_REFRESH_SECONDS:
        rules = row.get("rules", {})
    else:
        details = api.market_details(epic)
        rules = details.get("dealingRules", {})
        rules_cache[epic] = {"ts": now, "rules": rules}

    min_size = float(rules.get("minDealSize", {}).get("value", configured_size) or configured_size)
    max_size = float(rules.get("maxDealSize", {}).get("value", configured_size) or configured_size)
    increment = float(rules.get("minSizeIncrement", {}).get("value", 0) or 0)

    size = max(float(configured_size), min_size)
    if increment > 0:
        steps = round(size / increment)
        size = steps * increment
        if size < min_size:
            size = min_size
    if size > max_size:
        raise RuntimeError(f"SIZE_TOO_LARGE configured={configured_size} max={max_size}")
    return size


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


def refresh_history(api, epic, cache, resolution):
    now = time.time()
    key = f"{epic}:{resolution}"
    row = cache.get(key)
    if row and now - row.get("ts", 0) < HISTORY_REFRESH_SECONDS:
        return row.get("xs", [])
    raw = api.prices(epic, 100, resolution=resolution)
    xs = candles(raw)
    if xs:
        cache[key] = {"ts": now, "xs": xs[-120:]}
    return cache.get(key, {}).get("xs", [])


def refresh_history_budget(api, epics, cache, budget=6):
    refreshed = 0
    # Prioritize the requested non-FX instruments so a large FX universe
    # cannot starve GOLD/SILVER/US500/US100/US1000 from fresh M5/M15 data.
    priority = {"GOLD", "SILVER", "US500", "US100", "US1000"}
    ordered = sorted(epics, key=lambda e: (0 if str(e).upper() in priority else 1, str(e)))
    for epic in ordered:
        if refreshed >= budget:
            break
        try:
            m5_key = f"{epic}:{PRICE_RESOLUTION}"
            m15_key = f"{epic}:{TREND_RESOLUTION}"
            now = time.time()
            m5_fresh = m5_key in cache and now - cache[m5_key].get("ts", 0) < HISTORY_REFRESH_SECONDS
            m15_fresh = m15_key in cache and now - cache[m15_key].get("ts", 0) < HISTORY_REFRESH_SECONDS
            if m5_fresh and m15_fresh:
                continue
            refresh_history(api, epic, cache, PRICE_RESOLUTION)
            refresh_history(api, epic, cache, TREND_RESOLUTION)
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
    market_cache = {"ts": 0.0, "markets": []}
    position_cache = {"ts": 0.0, "positions": []}
    market_rules = {}
    order_cooldown = {}

    log.info(
        "HYBRID BOT | DRY_RUN=%s | SCAN=2s | ENTRY=M5+M15 | FX+GOLD+SILVER+US500+US100/US1000 | MAX_LOSS=%.2f AED",
        DRY_RUN,
        MAX_INITIAL_LOSS,
    )

    while True:
        try:
            # One market snapshot covers all currency instruments and avoids
            # hammering the REST API once per pair every 2 seconds.
            now = time.time()
            if now - market_cache["ts"] >= MARKET_REFRESH_SECONDS or not market_cache["markets"]:
                try:
                    market_cache["markets"] = tradable_markets(api.markets())
                    market_cache["ts"] = now
                except Exception as e:
                    log.warning("MARKETS SNAPSHOT FAILED | %s | using cached markets", e)
            markets = market_cache["markets"]
            market_by_epic = {m["epic"]: m for m in markets}

            if now - position_cache["ts"] >= POSITION_REFRESH_SECONDS:
                try:
                    position_cache["positions"] = api.positions()
                    position_cache["ts"] = now
                except Exception as e:
                    log.error("POSITIONS SNAPSHOT FAILED | %s | keeping last snapshot", e)
            positions = position_cache["positions"]

            owned = owned_open_positions(positions, state)
            refresh_history_budget(api, list(market_by_epic), history, budget=6)

            # Profit protection runs first, every 2 seconds.
            for deal_id, item in owned.items():
                epic = item.get("market", {}).get("epic")
                if epic in market_by_epic:
                    tighten_profit_stop(api, item, state["owned"][deal_id], market_by_epic[epic])

            # Entries use the same M5+M15 signal for FX and the requested
            # metals/indices. Existing positions on the same epic block a new
            # entry, including manual/other-bot positions.
            for epic, market in market_by_epic.items():
                try:
                    m5 = history.get(f"{epic}:{PRICE_RESOLUTION}", {}).get("xs", [])
                    m15 = history.get(f"{epic}:{TREND_RESOLUTION}", {}).get("xs", [])
                    if len(m5) < 16 or len(m15) < 16:
                        continue

                    px = current_price(market)
                    if px is None:
                        continue

                    # Inject the current quote into the minute history for a
                    # fresh decision without requesting history every 2 sec.
                    working_m5 = (m5 + [px])[-120:]
                    sig = signal(working_m5, m15)

                    existing_any = any(
                        x.get("market", {}).get("epic") == epic
                        for x in positions
                    )

                    if sig and not existing_any:
                        last_order = order_cooldown.get(epic, 0.0)
                        if time.time() - last_order < ORDER_COOLDOWN_SECONDS:
                            continue
                        try:
                            order_size = normalize_size(api, epic, SIZE, market_rules)
                            if order_size != SIZE:
                                log.info("SIZE NORMALIZED | %s | configured=%.4f -> broker_min=%.4f", epic, SIZE, order_size)
                            open_bot_position(api, epic, sig, order_size, state, market)
                            order_cooldown[epic] = time.time()
                            log.info("ENTRY | %s -> %s | size=%.4f", epic, sig, order_size)
                        except Exception as e:
                            log.warning("%s ENTRY FAILED | %s", epic, e)
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
