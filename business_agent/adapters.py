""""Provider adapters for the UAE Business AI Agent."""
from __future__ import annotations
from typing import Protocol
import requests

class MarketResearch(Protocol):
    def search(self, query: str) -> list[dict]: ...

class CRM(Protocol):
    def create_lead(self, data: dict) -> dict: ...
    def update(self, lead_id: str, data: dict) -> dict: ...

class Messenger(Protocol):
    def send(self, recipient: str, subject: str, message: str, html: str | None = None) -> dict: ...
    def list_messages(self, limit: int = 20) -> list[dict]: ...
    def get_message(self, message_id: str) -> dict: ...

class Payments(Protocol):
    def create_invoice(self, customer: dict, amount_aed: float, description: str) -> dict: ...
    def payment_status(self, payment_id: str) -> dict: ...

class TavilyMarketResearch:
    def __init__(self, api_key: str): self.api_key = api_key
    def search(self, query: str) -> list[dict]:
        if not self.api_key: return []
        r = requests.post("https://api.tavily.com/search",
            json={"api_key": self.api_key, "query": query, "search_depth": "advanced", "max_results": 10},
            timeout=30)
        r.raise_for_status()
        return r.json().get("results", [])

class HubSpotCRM:
    def __init__(self, access_token: str):
        self.base = "https://api.hubapi.com"
        self.headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}
    def create_lead(self, data: dict) -> dict:
        r = requests.post(f"{self.base}/crm/v3/objects/contacts", headers=self.headers,
                           json={"properties": data}, timeout=30)
        r.raise_for_status()
        return r.json()
    def update(self, lead_id: str, data: dict) -> dict:
        r = requests.patch(f"{self.base}/crm/v3/objects/contacts/{lead_id}", headers=self.headers,
                           json={"properties": data}, timeout=30)
        r.raise_for_status()
        return r.json()

class AgentMailMessenger:
    """AgentMail API adapter. Resolves the inbox by email before every operation."""
    def __init__(self, api_key: str, inbox_id: str):
        self.base = "https://api.agentmail.to/v0"
        self.inbox_id = inbox_id
        self.headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    def _resolve_inbox_id(self) -> str:
        r = requests.get(f"{self.base}/inboxes", headers=self.headers,
                         params={"limit": 100}, timeout=30)
        r.raise_for_status()
        data = r.json()
        inboxes = data.get("inboxes", data if isinstance(data, list) else [])
        for inbox in inboxes:
            email = inbox.get("email") or inbox.get("address") or ""
            if email.lower() == self.inbox_id.lower():
                return inbox.get("inbox_id") or inbox.get("id") or self.inbox_id
        raise RuntimeError(f"AgentMail inbox not visible to this API key: {self.inbox_id}")

    def list_messages(self, limit: int = 20) -> list[dict]:
        inbox_id = self._resolve_inbox_id()
        r = requests.get(f"{self.base}/inboxes/{inbox_id}/messages",
                         headers=self.headers,
                         params={"limit": min(limit, 100), "ascending": "true"},
                         timeout=30)
        r.raise_for_status()
        data = r.json()
        return data.get("messages", data if isinstance(data, list) else [])

    def get_message(self, message_id: str) -> dict:
        inbox_id = self._resolve_inbox_id()
        r = requests.get(f"{self.base}/inboxes/{inbox_id}/messages/{message_id}",
                         headers=self.headers, timeout=30)
        r.raise_for_status()
        return r.json()

    def send(self, recipient: str, subject: str, message: str, html: str | None = None) -> dict:
        inbox_id = self._resolve_inbox_id()
        payload = {"to": [recipient], "subject": subject, "text": message}
        if html:
            payload["html"] = html
        r = requests.post(f"{self.base}/inboxes/{inbox_id}/messages/send",
                          headers=self.headers, json=payload, timeout=30)
        r.raise_for_status()
        return r.json()

class StripePayments:
    def __init__(self, secret_key: str):
        self.base = "https://api.stripe.com/v1"
        self.auth = (secret_key, "")
    def create_invoice(self, customer: dict, amount_aed: float, description: str) -> dict:
        customer_id = customer.get("stripe_customer_id")
        if not customer_id:
            r = requests.post(f"{self.base}/customers", auth=self.auth,
                               data={"name": customer.get("name",""), "email": customer.get("email","")}, timeout=30)
            r.raise_for_status()
            customer_id = r.json()["id"]
        price = requests.post(f"{self.base}/prices", auth=self.auth,
            data={"currency":"aed", "unit_amount":int(round(amount_aed*100)),
                  "product_data[name]":description[:250]}, timeout=30)
        price.raise_for_status()
        item = requests.post(f"{self.base}/invoiceitems", auth=self.auth,
            data={"customer":customer_id, "pricing[price]":price.json()["id"]}, timeout=30)
        if not item.ok:
            try:
                err = item.json().get("error", {})
                message = err.get("message") or item.text
                code = err.get("code")
                param = err.get("param")
                details = f"Stripe invoice item failed: {message}"
                if code:
                    details += f" | code={code}"
                if param:
                    details += f" | param={param}"
                raise RuntimeError(details)
            except ValueError:
                item.raise_for_status()
        item.raise_for_status()
        inv = requests.post(f"{self.base}/invoices", auth=self.auth,
            data={"customer":customer_id, "auto_advance":"false"}, timeout=30)
        inv.raise_for_status()
        invoice = inv.json()
        invoice_id = invoice["id"]
        finalized = requests.post(f"{self.base}/invoices/{invoice_id}/finalize",
            auth=self.auth, timeout=30)
        finalized.raise_for_status()
        return finalized.json()
    def payment_status(self, payment_id: str) -> dict:
        r = requests.get(f"{self.base}/invoices/{payment_id}", auth=self.auth, timeout=30)
        r.raise_for_status()
        return r.json()
