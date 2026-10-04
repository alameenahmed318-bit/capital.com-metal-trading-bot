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
MARKET_REFRESH_SECONDS = 2
POSITION_REFRESH_SECONDS = 2
PRICE_RESOLUTION = "MINUTE_5"
TREND_RESOLUTION = "MINUTE_15"
RUN_SECONDS = int(os.getenv("RUN_SECONDS", "720"))
HISTORY_REFRESH_SECONDS = int(os.getenv("HISTORY_REFRESH_SECONDS", "60"))
MAX_INITIAL_LOSS = float(os.getenv("MAX_INITIAL_LOSS_AED", "10"))
# Staged profit protection:
# +0.25 AED = arm protection, +0.50 AED = raise the protected floor,
# +1.00 AED = activate the normal trailing stage.
PROFIT_PROTECT_TRIGGER = float(os.getenv("PROFIT_PROTECT_TRIGGER_AED", "0.25"))
PROFIT_STAGE_2 = float(os.getenv("PROFIT_STAGE_2_AED", "0.50"))
PROFIT_ARM = float(os.getenv("PROFIT_ARM_AED", "1.00"))
PROFIT_FLOOR = float(os.getenv("PROFIT_FLOOR_AED", "0.05"))
PROFIT_LOCK = float(os.getenv("PROFIT_LOCK_AED", "0.10"))
TRAIL_GIVEBACK = float(os.getenv("TRAIL_GIVEBACK_AED", "0.75"))
PROFIT_TRAIL_RATIO = float(os.getenv("PROFIT_TRAIL_RATIO", "0.30"))
STRONG_RETRACE_RATIO = float(os.getenv("STRONG_RETRACE_RATIO", "0.35"))
ADD_ON_MIN_PROFIT_AED = float(os.getenv("ADD_ON_MIN_PROFIT_AED", "0.20"))
ADD_ON_MIN_MOVE_RATIO = float(os.getenv("ADD_ON_MIN_MOVE_RATIO", "0.25"))
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
            epic in {"SILVER", "US500", "US100", "US1000"}
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
    """Fast local profit protection with broker-stop as a second layer."""
    p = item.get("position", {})
    deal_id = p.get("dealId")
    direction = str(p.get("direction", "")).upper()
    upl = float(p.get("upl", 0) or 0)
    entry = float(p.get("level", 0) or 0)
    px = current_price(market)

    if not deal_id or direction not in ("BUY", "SELL") or px is None or entry <= 0:
        return

    if upl <= -MAX_INITIAL_LOSS:
        try:
            if DRY_RUN:
                log.info("DRY RUN | MAX LOSS CLOSE | %s | UPL=%.2f", deal_id, upl)
            else:
                api.close_position(deal_id)
                log.warning("MAX LOSS CLOSE | %s | UPL=%.2f", deal_id, upl)
            state_entry["close_requested"] = True
        except Exception as e:
            log.warning("MAX LOSS CLOSE FAILED | %s | %s", deal_id, e)
        return

    peak = max(float(state_entry.get("peak_upl", 0) or 0), upl)
    state_entry["peak_upl"] = peak

    if peak >= PROFIT_PROTECT_TRIGGER:
        state_entry["protected_profit"] = True

    if not state_entry.get("protected_profit", False):
        return

    # Dynamic floor: as peak profit grows, more of that profit is protected.
    if peak >= PROFIT_ARM:
        locked_profit = max(PROFIT_LOCK, peak * (1.0 - PROFIT_TRAIL_RATIO))
    elif peak >= PROFIT_STAGE_2:
        locked_profit = max(PROFIT_FLOOR, peak * 0.40)
    else:
        locked_profit = PROFIT_FLOOR

    # Local close is the authoritative fallback. It works even when Capital
    # rejects a broker stop because of instrument-specific stop distances.
    if upl <= locked_profit:
        try:
            if DRY_RUN:
                log.info(
                    "DRY RUN | PROFIT CLOSE | %s | UPL=%.2f | PEAK=%.2f | FLOOR=%.2f",
                    deal_id, upl, peak, locked_profit
                )
            else:
                api.close_position(deal_id)
                log.info(
                    "PROFIT CLOSE | %s | UPL=%.2f | PEAK=%.2f | FLOOR=%.2f",
                    deal_id, upl, peak, locked_profit
                )
            state_entry["close_requested"] = True
        except Exception as e:
            log.warning("PROFIT CLOSE FAILED | %s | UPL=%.2f | %s", deal_id, upl, e)
        return

    vpp = value_per_price(p, px)
    if not vpp:
        return

    target = (
        entry + locked_profit / vpp
        if direction == "BUY"
        else entry - locked_profit / vpp
    )
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
        state_entry["stop_level"] = target
        log.info(
            "DRY RUN | PROFIT PROTECT | %s | UPL=%.2f | PEAK=%.2f | FLOOR=%.2f | STOP=%.8f",
            deal_id, upl, peak, locked_profit, target
        )
        return

    try:
        api.update_position(deal_id, target)
        state_entry["stop_level"] = target
        state_entry["last_stop_update_ok"] = True
        log.info(
            "PROFIT PROTECT | %s | UPL=%.2f | PEAK=%.2f | FLOOR=%.2f | STOP=%.8f",
            deal_id, upl, peak, locked_profit, target
        )
    except Exception as e:
        state_entry["last_stop_update_ok"] = False
        log.warning("BROKER PROFIT STOP FAILED | %s | %s", deal_id, e)


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
                "protected_profit": False,
                "last_stop_update_ok": False,
            }
            log.info("OWNED POSITION | %s | %s", deal_id, epic)


def reconcile_owned_state(positions, state):
    # Intentionally do not adopt arbitrary live positions.
    # Ownership is created only from this bot's own order confirmation.
    return

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


def refresh_history_budget(api, markets, cache, budget=6):
    refreshed = 0
    # Never let the large FX universe starve metals/indices. Capital epics are
    # not guaranteed to be literally GOLD/SILVER/US100, so classify by both
    # epic and instrument name.
    priority = []
    other = []
    for market in markets:
        epic = str(market.get("epic", "")).upper()
        name = str(market.get("instrumentName", market.get("name", ""))).upper()
        is_priority = (
            epic in {"SILVER", "US500", "US100", "US1000"}
            or "SILVER" in name
            or "US TECH 100" in name
            or "TECH 100" in name
            or "S&P 500" in name
            or "US 500" in name
        )
        (priority if is_priority else other).append(epic)
    # Refresh every requested metal/index first; only then spend remaining
    # budget on FX. This fixes the silent starvation where GOLD/SILVER could
    # be tradable but never receive enough M5/M15 history to generate a signal.
    ordered = priority + sorted(other)
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
    pending_epics = {}

    log.info(
        "HYBRID BOT | DRY_RUN=%s | SCAN=2s | ENTRY=M5+M15 | PROFIT_PROTECT=+%.2f/+%.2f/+%.2f | FX+SILVER+US500+US100/US1000 | ADD_ON>=%.2f AED | MAX_LOSS=%.2f AED",
        DRY_RUN,
        PROFIT_PROTECT_TRIGGER,
        PROFIT_STAGE_2,
        PROFIT_ARM,
        ADD_ON_MIN_PROFIT_AED,
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

            live_epics = {
                x.get("market", {}).get("epic")
                for x in positions
                if x.get("position", {}).get("dealId")
            }
            for locked_epic in list(pending_epics):
                if locked_epic in live_epics:
                    pending_epics[locked_epic] = time.time()
                elif time.time() - pending_epics[locked_epic] > 120:
                    del pending_epics[locked_epic]

            reconcile_owned_state(positions, state)
            owned = owned_open_positions(positions, state)
            refresh_history_budget(api, list(market_by_epic.values()), history, budget=6)

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

                    same_epic = [
                        x for x in positions
                        if x.get("market", {}).get("epic") == epic
                    ]
                    same_direction = [
                        x for x in same_epic
                        if str(x.get("position", {}).get("direction", "")).upper() == sig
                    ]
                    opposite_direction = [
                        x for x in same_epic
                        if str(x.get("position", {}).get("direction", "")).upper() != sig
                    ]

                    can_open = not same_epic and epic not in pending_epics
                    add_on = False

                    # Add only when the existing position is already profitable
                    # and the live price has extended in the same direction.
                    if same_direction and not opposite_direction:
                        profitable = [
                            x for x in same_direction
                            if float(x.get("position", {}).get("upl", 0) or 0) >= ADD_ON_MIN_PROFIT_AED
                        ]
                        levels = [
                            float(x.get("position", {}).get("level", 0) or 0)
                            for x in profitable
                            if x.get("position", {}).get("level") is not None
                        ]
                        if levels:
                            avg_entry = sum(levels) / len(levels)
                            recent = m5[-4:]
                            recent_range = max(max(recent) - min(recent), 1e-12)
                            extension = (px - avg_entry) if sig == "BUY" else (avg_entry - px)
                            add_on = extension >= recent_range * ADD_ON_MIN_MOVE_RATIO
                            can_open = add_on

                    if sig and can_open:
                        last_order = order_cooldown.get(epic, 0.0)
                        if time.time() - last_order < ORDER_COOLDOWN_SECONDS:
                            continue
                        try:
                            order_size = normalize_size(api, epic, SIZE, market_rules)
                            if order_size != SIZE:
                                log.info("SIZE NORMALIZED | %s | configured=%.4f -> broker_min=%.4f", epic, SIZE, order_size)
                            pending_epics[epic] = time.time()
                            open_bot_position(api, epic, sig, order_size, state, market)
                            try:
                                position_cache["positions"] = api.positions()
                                position_cache["ts"] = time.time()
                                pending_epics.pop(epic, None)
                            except Exception:
                                pass
                            order_cooldown[epic] = time.time()
                            log.info(
                                "ENTRY | %s -> %s | size=%.4f | mode=%s | existing=%d",
                                epic, sig, order_size,
                                "ADD_ON" if add_on else "INITIAL",
                                len(same_epic)
                            )
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
