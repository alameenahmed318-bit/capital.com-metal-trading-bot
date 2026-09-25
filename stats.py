import json
import sqlite3
from datetime import datetime, timezone

import config

STATS_PATH = "stats.json"


def _epic_stats(rows):
    trades = len(rows)
    winners = sum(1 for r in rows if r > 0)
    losers = sum(1 for r in rows if r < 0)
    pnl = sum(rows)
    win_rate = round((winners / trades) * 100, 1) if trades else 0
    return {
        "trades": trades,
        "winners": winners,
        "losers": losers,
        "win_rate": win_rate,
        "pnl": round(pnl, 2),
    }


def update_stats():
    # Trades logged before the `strategy` column existed are NULL there; treat those
    # as "baseline" so historical trades stay counted under the strategy that was
    # actually live at the time, rather than vanishing or blending into vol_regime.
    strategy_filter = "(strategy = ? OR (strategy IS NULL AND ? = 'baseline'))"
    params = (config.STRATEGY, config.STRATEGY)

    with sqlite3.connect(config.DB_PATH) as conn:
        opened_count = conn.execute(
            f"SELECT COUNT(*) FROM trades WHERE status IN ('OPENED', 'CLOSED') AND {strategy_filter}",
            params,
        ).fetchone()[0]
        open_count = conn.execute(
            f"SELECT COUNT(*) FROM trades WHERE status = 'OPENED' AND close_price IS NULL AND {strategy_filter}",
            params,
        ).fetchone()[0]
        closed_rows = conn.execute(
            f"SELECT epic, pnl FROM trades WHERE close_price IS NOT NULL AND {strategy_filter} ORDER BY id ASC",
            params,
        ).fetchall()

    closed_pnls = [pnl for _, pnl in closed_rows]
    closed_trades = len(closed_pnls)
    winners = [p for p in closed_pnls if p > 0]
    losers = [p for p in closed_pnls if p < 0]
    win_rate = round((len(winners) / closed_trades) * 100, 1) if closed_trades else 0
    loss_rate = round((len(losers) / closed_trades) * 100, 1) if closed_trades else 0
    average_win = round(sum(winners) / len(winners), 2) if winners else 0
    average_loss = round(sum(losers) / len(losers), 2) if losers else 0
    expectancy = round((win_rate / 100 * average_win) - (loss_rate / 100 * abs(average_loss)), 2)

    peak = 0
    cumulative = 0
    max_drawdown = 0
    for pnl in closed_pnls:
        cumulative += pnl
        peak = max(peak, cumulative)
        max_drawdown = min(max_drawdown, cumulative - peak)

    streak = 0
    for pnl in reversed(closed_pnls):
        if pnl > 0:
            if streak < 0:
                break
            streak += 1
        elif pnl < 0:
            if streak > 0:
                break
            streak -= 1
        else:
            break

    # Use the full epic list, not config.EPICS - that narrows to ["GOLD"] under
    # vol_regime, which would silently drop SILVER/COPPER's baseline-strategy history
    # from the per-epic breakdown.
    by_epic = {
        epic: _epic_stats([pnl for e, pnl in closed_rows if e == epic])
        for epic in config.EPICS
    }

    stats = {
        "last_updated": datetime.now(timezone.utc).isoformat(),
        "overall": {
            "total_trades": opened_count,
            "open_trades": open_count,
            "closed_trades": closed_trades,
            "winners": len(winners),
            "losers": len(losers),
            "win_rate": win_rate,
            "total_pnl": round(sum(closed_pnls), 2),
            "best_trade": round(max(closed_pnls), 2) if closed_pnls else 0,
            "worst_trade": round(min(closed_pnls), 2) if closed_pnls else 0,
            "average_win": average_win,
            "average_loss": average_loss,
            "expectancy": expectancy,
            "max_drawdown": round(max_drawdown, 2),
            "current_streak": streak,
        },
        "by_epic": by_epic,
    }

    with open(STATS_PATH, "w") as f:
        json.dump(stats, f, indent=2)

    return stats


if __name__ == "__main__":
    print(json.dumps(update_stats(), indent=2))
