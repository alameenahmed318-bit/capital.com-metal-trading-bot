import os
from dataclasses import dataclass

def env_bool(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).lower() == "true"

@dataclass(frozen=True)
class Settings:
    app_name: str = os.getenv("AGENT_NAME", "UAE Business AI Agent")
    timezone: str = os.getenv("AGENT_TIMEZONE", "Asia/Dubai")
    dry_run: bool = env_bool("AGENT_DRY_RUN", True)
    approval_required_for_money: bool = env_bool("APPROVAL_REQUIRED_FOR_MONEY", True)
    approval_required_for_contracts: bool = env_bool("APPROVAL_REQUIRED_FOR_CONTRACTS", True)
    market_scan_minutes: int = int(os.getenv("MARKET_SCAN_MINUTES", "60"))
    state_file: str = os.getenv("AGENT_STATE_FILE", "business_agent/state.json")
    stripe_secret_key: str = os.getenv("STRIPE_SECRET_KEY", "")
    hubspot_access_token: str = os.getenv("HUBSPOT_ACCESS_TOKEN", "")
    tavily_api_key: str = os.getenv("TAVILY_API_KEY", "")
    agentmail_api_key: str = os.getenv("AGENTMAIL_API_KEY", "")
    agentmail_inbox_id: str = os.getenv("AGENTMAIL_INBOX_ID", "uaebusinessai@agentmail.to")
    company_name: str = os.getenv("COMPANY_NAME", "UAE Business AI")
