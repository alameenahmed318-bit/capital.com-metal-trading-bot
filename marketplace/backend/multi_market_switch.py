"""Market control contract for the unified AL-ZOOL project.

This module does not transmit orders or change MT5 settings. The HTTP/MT5
integration must use isolated workers and persist the selected market. A
transition refuses to authorize a new market until both terminals prove their
scope and outstanding entry authorizations in the old market have expired.
"""
from dataclasses import dataclass
from math import isfinite
from threading import RLock
import time

from market_symbols import market_family

MARKETS=("XAUUSD","BTCUSD")
MAX_TELEMETRY_AGE=30.0
ENTRY_AUTH_TTL=15.0

def market_scope(owner,market):
    if not owner or market not in MARKETS:
        raise ValueError("Invalid market scope")
    return (str(owner),market)

@dataclass(frozen=True)
class Terminal:
    symbol:str
    account_login:str
    broker_server:str
    seen_at:float
    existing_positions:int=0
    ready:bool=False
    other_symbol_positions:int=0
    other_symbol_orders:int=0

    @property
    def market(self):
        return market_family(self.symbol)

    @property
    def account(self):
        return (self.broker_server.strip().casefold(),self.account_login.strip())

    def fresh(self,now):
        return bool(self.ready and self.market in MARKETS and all(self.account)
                    and isfinite(self.seen_at) and 0<=now-self.seen_at<MAX_TELEMETRY_AGE
                    and self.other_symbol_positions==0 and self.other_symbol_orders==0)

class MarketSwitch:
    """One entry market per owner; existing-position management stays scoped.

    The object is deliberately independent of global CONNECTIONS and the
    existing account-wide basket functions. Pass verified server timestamps,
    never untrusted browser-supplied account readiness.
    """
    def __init__(self,selected=None):
        if selected is not None and selected not in MARKETS:
            raise ValueError("Unsupported market")
        self.selected=selected
        self.active=selected
        self._pending=None
        self._not_before=0.0
        self._issued_until={m:0.0 for m in MARKETS}
        self._terminals={}
        self._lock=RLock()

    def observe(self,terminal):
        if not isinstance(terminal,Terminal) or terminal.market not in MARKETS:
            raise ValueError("Unsupported terminal")
        with self._lock:
            self._terminals[terminal.market]=terminal

    def _distinct_accounts(self):
        a,b=(self._terminals.get(m) for m in MARKETS)
        return not(a and b and all(a.account) and a.account==b.account)

    def _ready(self,market,now):
        t=self._terminals.get(market)
        return bool(t and t.fresh(now) and self._distinct_accounts())

    def choose(self,market,now=None):
        now=time.time() if now is None else float(now)
        if market not in MARKETS or not isfinite(now):
            raise ValueError("Unsupported market or timestamp")
        with self._lock:
            if not self._ready(market,now):
                # Reject invalid selections without disabling the former market.
                return {"accepted":False,"reason":"TARGET_NOT_READY_OR_SHARED_ACCOUNT",
                        **self.status(now)}
            if self.active==market and self._pending is None:
                return {"accepted":True,**self.status(now)}
            previous=self.active
            self.selected=market
            self.active=None
            self._pending=market
            self._not_before=max(now,max(self._issued_until.values()))
            self._advance(now)
            return {"accepted":True,"previous":previous,**self.status(now)}

    def _advance(self,now):
        if self._pending and now>=self._not_before and self._ready(self._pending,now):
            self.active=self._pending
            self._pending=None

    def entry_allowed(self,market,now=None):
        now=time.time() if now is None else float(now)
        with self._lock:
            self._advance(now)
            return bool(market==self.active and self._ready(market,now))

    def issue_entry(self,market,now=None,expires_at=None):
        """Reserve a real permit before a response leaves the server.
        Invalid or too-long permits are not issued.
        """
        now=time.time() if now is None else float(now)
        expires_at=now+ENTRY_AUTH_TTL if expires_at is None else float(expires_at)
        if not isfinite(expires_at) or not now<expires_at<=now+ENTRY_AUTH_TTL:
            return False
        with self._lock:
            if not self.entry_allowed(market,now):
                return False
            self._issued_until[market]=max(self._issued_until[market],expires_at)
            return True

    def management_allowed(self,market,now=None):
        """Allow existing trades to be managed independently, without
        authorizing any new exposure; actual execution guards still apply.
        """
        now=time.time() if now is None else float(now)
        with self._lock:
            return self._ready(market,now)

    def status(self,now=None):
        now=time.time() if now is None else float(now)
        with self._lock:
            self._advance(now)
            return {"selected":self.selected,"active":self.active,
                    "switching":self._pending is not None,"not_before":self._not_before,
                    "markets":{m:{"ready":self._ready(m,now),
                        "symbol":self._terminals[m].symbol if m in self._terminals else None,
                        "open_positions":self._terminals[m].existing_positions if m in self._terminals else None,
                        "new_entries_allowed":self.active==m and self._ready(m,now)}
                        for m in MARKETS}}
