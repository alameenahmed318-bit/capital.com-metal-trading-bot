"""AL-ZOOL role separation: do not expose central strategies to clients."""
from pathlib import PurePosixPath
from urllib.parse import unquote

# Only these presentation assets can be served without a file-system path lookup.
# Server modules, .env, SQL, MT5 source, Git metadata, and downloads are private.
PUBLIC_UI_ASSETS=frozenset("""
index.html prime.html dashboard.html trade-management.html trade-history.html
ai-analysis.html learning-lab.html backtest-lab.html opportunities.html bots-library.html
app.js dashboard.js prime.js admin-navigation.js trade-management.js
numbers-en.js ui-shell.js
styles.css admin-theme.css dashboard.css dashboard-skin.css trade-management.css
prime.css prime-embedded.css ui-shell.css ui-navigation.css alzool-studio.css
alzool-dark-ai.css
""".split())
PROTECTED_OWNER_ASSETS=frozenset({"strategy-engine.js"})
OWNER_READ_ENDPOINTS=frozenset("""
/api/bot-advisors /api/backtest/history /api/backtest/learning
/api/market/context /api/opportunities/events /api/ai/actions
/api/ai/learning /api/ai/council /api/trade-management/settings
""".split())
OWNER_WRITE_ENDPOINTS=frozenset({
    "/api/backtest/learning", "/api/opportunities/events", "/api/trade-management/settings"
})


def public_static(path):
    # Prevent percent-encoded traversal, platform-specific separators and directories.
    if not isinstance(path,str) or path in ("/", "/index.html"):
        return True
    decoded=unquote(path)
    if decoded!=path or "\\" in decoded or "//" in decoded or "\x00" in decoded:
        return False
    if not decoded.startswith("/") or PurePosixPath(decoded).parent!=PurePosixPath("/"):
        return False
    return decoded[1:] in PUBLIC_UI_ASSETS


def minimal_terminal_decision(full):
    """Only authorization facts/price needed by the EA; never central votes."""
    keys=("signal","confidence","strategy_id","combination_id","bar_time",
          "entry_reference_price","entry_atr","execution_ready","trading_enabled",
          "authorization_id","authorization_expires_at")
    result={k:full[k] for k in keys if k in full}
    result["reason_ar"]="تم إرسال قرار التنفيذ" if full.get("execution_ready") else "انتظار موافقة المحرك والحماية"
    result["message"]=result["reason_ar"]
    return result


def basic_settings(full):
    keys=("single_enabled","single_profit_target","group_enabled","group_profit_target",
          "stop_loss_enabled","stop_loss_mode","stop_loss_value")
    return {k:full.get(k) for k in keys}
