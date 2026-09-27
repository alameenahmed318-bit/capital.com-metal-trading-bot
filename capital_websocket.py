"""Capital.com WebSocket live-price feed.

Keeps the latest executable bid/offer for up to 40 epics. The REST API remains
the source of market metadata and historical candles; this module supplies the
freshest quote for entry/position-management decisions.
"""
from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timezone

try:
    import websocket
except ImportError:  # pragma: no cover
    websocket = None


STREAM_URL = "wss://api-streaming-capital.backend-capital.com/connect"


class CapitalLivePriceStream:
    def __init__(self, cst, security_token, epics, log_fn=print):
        if websocket is None:
            raise RuntimeError("websocket-client is required for live Capital.com prices")
        self.cst = cst
        self.security_token = security_token
        self.epics = list(dict.fromkeys(epics))[:40]
        self.log = log_fn
        self._quotes = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._thread = None
        self._ws = None
        self._last_message = 0.0

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run, name="capital-live-prices", daemon=True)
        self._thread.start()
        self._ready.wait(timeout=8.0)

    def stop(self):
        self._stop.set()
        ws = self._ws
        if ws is not None:
            try:
                ws.close()
            except Exception:
                pass
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3.0)

    def get_quote(self, epic):
        with self._lock:
            q = self._quotes.get(epic)
            return dict(q) if q else None

    def age_seconds(self, epic):
        q = self.get_quote(epic)
        if not q:
            return None
        return max(0.0, time.time() - float(q["received_at"]))

    def _run(self):
        backoff = 1.0
        while not self._stop.is_set():
            try:
                self._ws = websocket.create_connection(
                    STREAM_URL,
                    timeout=15,
                    enable_multithread=True,
                )
                self._ws.send(json.dumps({
                    "destination": "marketData.subscribe",
                    "correlationId": "live-prices-1",
                    "cst": self.cst,
                    "securityToken": self.security_token,
                    "payload": {"epics": self.epics},
                }))
                self.log(f"LIVE WS: subscribed to {len(self.epics)} markets")
                self._ready.set()
                backoff = 1.0
                last_ping = time.monotonic()

                while not self._stop.is_set():
                    if time.monotonic() - last_ping >= 240:
                        self._ws.send(json.dumps({
                            "destination": "ping",
                            "correlationId": str(int(time.time())),
                            "cst": self.cst,
                            "securityToken": self.security_token,
                        }))
                        last_ping = time.monotonic()

                    try:
                        raw = self._ws.recv()
                    except Exception:
                        break
                    if not raw:
                        break

                    try:
                        msg = json.loads(raw)
                    except Exception:
                        continue

                    if msg.get("destination") != "quote":
                        continue

                    payload = msg.get("payload") or {}
                    epic = payload.get("epic")
                    bid = payload.get("bid")
                    offer = payload.get("ofr", payload.get("offer", payload.get("ask")))
                    if not epic or bid is None or offer is None:
                        continue
                    try:
                        bid = float(bid)
                        offer = float(offer)
                    except (TypeError, ValueError):
                        continue
                    if bid <= 0 or offer <= 0 or offer < bid:
                        continue

                    now = time.time()
                    with self._lock:
                        self._quotes[epic] = {
                            "bid": bid,
                            "offer": offer,
                            "timestamp": payload.get("timestamp"),
                            "received_at": now,
                            "received_at_iso": datetime.now(timezone.utc).isoformat(),
                        }
                    self._last_message = now

            except Exception as exc:
                self.log(f"LIVE WS: connection error: {exc}")
                self._ready.set()
            finally:
                ws = self._ws
                self._ws = None
                if ws is not None:
                    try:
                        ws.close()
                    except Exception:
                        pass

            if not self._stop.is_set():
                time.sleep(backoff)
                backoff = min(15.0, backoff * 2.0)

        self.log("LIVE WS: stopped")
