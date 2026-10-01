"""Core orchestration loop for the UAE Business AI Agent.

This is intentionally provider-neutral: connectors for web research, CRM,
email, WhatsApp and payments are adapters. Sensitive actions are gated.
"""

from __future__ import annotations
import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .config import Settings

@dataclass
class Event:
    ts: float
    kind: str
    message: str
    data: dict[str, Any]

class BusinessAgent:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or Settings()
        self.events: list[Event] = []
        self.state: dict[str, Any] = {
            "status": "ready",
            "leads": [],
            "requests": [],
            "tasks": [],
            "payments": [],
            "approvals": [],
        }
        self._load()

    def log(self, kind: str, message: str, **data: Any) -> None:
        event = Event(time.time(), kind, message, data)
        self.events.append(event)
        self.events = self.events[-500:]
        self.state["last_event"] = asdict(event)
        self._save()

    def _load(self) -> None:
        path = Path(self.settings.state_file)
        if path.exists():
            try:
                self.state.update(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, json.JSONDecodeError):
                pass

    def _save(self) -> None:
        path = Path(self.settings.state_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.state, ensure_ascii=False, indent=2), encoding="utf-8")

    def create_request(self, company: str, service: str, details: str = "") -> dict[str, Any]:
        request = {
            "id": f"REQ-{int(time.time())}",
            "company": company,
            "service": service,
            "details": details,
            "status": "new",
        }
        self.state["requests"].append(request)
        self.log("request", f"New service request from {company}", request=request)
        return request

    def prepare_quote(self, request_id: str, amount_aed: float, scope: str) -> dict[str, Any]:
        quote = {
            "request_id": request_id,
            "amount_aed": amount_aed,
            "scope": scope,
            "status": "approval_required" if self.settings.approval_required_for_money else "ready",
        }
        if quote["status"] == "approval_required":
            self.state["approvals"].append({"type": "quote", "payload": quote})
        self.log("quote", "Quote prepared", quote=quote)
        return quote

    def request_approval(self, action: str, payload: dict[str, Any]) -> None:
        self.state["approvals"].append({"type": action, "payload": payload})
        self.log("approval", f"Approval required: {action}", payload=payload)

    def status(self) -> dict[str, Any]:
        return {
            "name": self.settings.app_name,
            "dry_run": self.settings.dry_run,
            "status": self.state.get("status", "ready"),
            "requests": len(self.state["requests"]),
            "leads": len(self.state["leads"]),
            "tasks": len(self.state["tasks"]),
            "payments": len(self.state["payments"]),
            "approvals": len(self.state["approvals"]),
            "last_event": self.state.get("last_event"),
        }

if __name__ == "__main__":
    agent = BusinessAgent()
    agent.state["status"] = "running"
    agent.log("system", "Business Agent started", dry_run=agent.settings.dry_run)
    print(json.dumps(agent.status(), ensure_ascii=False, indent=2))
