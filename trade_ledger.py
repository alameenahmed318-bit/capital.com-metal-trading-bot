"""Read-only, deduplicated broker trade ledger.

This module only reads Capital.com history. It never places, modifies, or closes
orders and never reconstructs P/L.
"""
from __future__ import annotations
import csv
import json
import os
from datetime import datetime, timedelta, timezone
from capital_api import CapitalAPI

LEDGER_JSON = "broker_trade_ledger.json"
LEDGER_CSV = "broker_trade_ledger.csv"
LOOKBACK_DAYS = max(1, int(os.environ.get("TRADE_LEDGER_LOOKBACK_DAYS", "30")))

def key(tx):
    return str(
        tx.get("reference")
        or tx.get("dealId")
        or tx.get("dealReference")
        or f"{tx.get('dateUtc') or tx.get('date') or ''}|{tx.get('instrumentName') or ''}|{tx.get('size') or ''}|{tx.get('note') or ''}"
    )

def main():
    api = CapitalAPI()
    api.login()
    now = datetime.now(timezone.utc)
    start = now - timedelta(days=LOOKBACK_DAYS)
    transactions = api.get_transactions(
        start.strftime("%Y-%m-%dT%H:%M:%S"),
        now.strftime("%Y-%m-%dT%H:%M:%S"),
        tx_type="TRADE",
    )

    existing = []
    if os.path.exists(LEDGER_JSON):
        try:
            with open(LEDGER_JSON, encoding="utf-8") as f:
                existing = json.load(f)
            if not isinstance(existing, list):
                existing = []
        except Exception:
            existing = []

    merged = {}
    for tx in existing:
        if isinstance(tx, dict):
            merged[key(tx)] = tx
    for tx in transactions:
        if isinstance(tx, dict):
            merged[key(tx)] = tx

    rows = list(merged.values())
    rows.sort(key=lambda x: str(x.get("dateUtc") or x.get("date") or ""), reverse=True)

    with open(LEDGER_JSON, "w", encoding="utf-8") as f:
        json.dump(rows, f, indent=2, ensure_ascii=False, default=str)

    fields = [
        "dateUtc", "date", "instrumentName", "transactionType", "note",
        "reference", "dealId", "dealReference", "size", "currency", "status",
        "pnl", "profitAndLoss", "realizedPnl", "cash",
    ]
    with open(LEDGER_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for tx in rows:
            writer.writerow({field: tx.get(field) for field in fields})

    print(f"TRADE LEDGER: fetched={len(transactions)} total_unique={len(rows)}")
    print("P/L policy: broker-reported only; no P/L inferred.")

if __name__ == "__main__":
    main()
