import os
from dataclasses import dataclass

@dataclass(frozen=True)
class Settings:
    app_name: str = os.getenv("AGENT_NAME", "UAE Business AI Agent")
    timezone: str = os.getenv("AGENT_TIMEZONE", "Asia/Dubai")
    dry_run: bool = os.getenv("AGENT_DRY_RUN", "true").lower() == "true"
    approval_required_for_money: bool = os.getenv("APPROVAL_REQUIRED_FOR_MONEY", "true").lower() == "true"
    approval_required_for_contracts: bool = os.getenv("APPROVAL_REQUIRED_FOR_CONTRACTS", "true").lower() == "true"
    market_scan_minutes: int = int(os.getenv("MARKET_SCAN_MINUTES", "60"))
    state_file: str = os.getenv("AGENT_STATE_FILE", "business_agent/state.json")
