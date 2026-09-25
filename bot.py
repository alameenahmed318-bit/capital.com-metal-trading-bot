def get_closed_trade_report(api, from_date, to_date):
    """Build the report from transactions plus detailed activity per trade."""
    transactions = api.get_transactions(from_date, to_date)
    wins = 0
    losses = 0
    flat = 0
    unknown_pnl = 0
    total_pnl = 0.0
    total_wins = 0.0
    total_losses = 0.0
    closed = 0

    for tx in transactions:
        transaction_type = str(tx.get("transactionType") or "").upper()
        note = str(
            tx.get("note")
            or tx.get("description")
            or transaction_type
            or ""
        ).lower()

        is_closed_trade = (
            "CLOSE" in transaction_type
            or "CLOSED" in transaction_type
            or "close" in note
            or "closed" in note
        )

        if not is_closed_trade:
            continue

        closed += 1

        # Search the COMPLETE transaction object case-insensitively.
        # Capital.com may return ProfitAndLoss / profitAndLoss /
        # profitLoss / PnL with different capitalization.
        pnl = _nested_numeric_profit_loss(tx)

        # If the transaction itself does not contain P/L, use the
        # detailed activity endpoint for this specific deal.
        if pnl is None:
            deal_id = (
                tx.get("dealId")
                or tx.get("dealReference")
                or tx.get("reference")
            )

            if deal_id:
                try:
                    activities = api.get_deal_activity(str(deal_id))

                    for activity in activities:
                        pnl = _nested_numeric_profit_loss(activity)
                        if pnl is not None:
                            break

                except Exception as exc:
                    log(
                        f"Detailed P/L lookup failed for "
                        f"{deal_id}: {exc}"
                    )

        if pnl is None:
            unknown_pnl += 1
            continue

        total_pnl += pnl

        if pnl > 0:
            wins += 1
            total_wins += pnl

        elif pnl < 0:
            losses += 1
            total_losses += pnl

        else:
            flat += 1

    return {
        "closed": closed,
        "wins": wins,
        "losses": losses,
        "flat": flat,
        "unknown_pnl": unknown_pnl,
        "total_pnl": round(total_pnl, 2),
        "total_wins": round(total_wins, 2),
        "total_losses": round(total_losses, 2),
    }
