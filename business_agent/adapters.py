"""Production-ready provider adapters. Credentials come from environment variables."""

from __future__ import annotations
from typing import Protocol
import requests

class MarketResearch(Protocol):
    def search(self, query: str) -> list[dict]: ...

class CRM(Protocol):
    def create_lead(self, data: dict) -> dict: ...
    def update(self, lead_id: str, data: dict) -> dict: ...

class Messenger(Protocol):
    def send(self, recipient: str, message: str) -> dict: ...

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
            data={"currency":"aed","unit_amount":int(round(amount_aed*100)),
                  "product_data[name]":description[:250]}, timeout=30)
        price.raise_for_status()
        item = requests.post(f"{self.base}/invoiceitems", auth=self.auth,
            data={"customer":customer_id,"price":price.json()["id"]}, timeout=30)
        item.raise_for_status()
        inv = requests.post(f"{self.base}/invoices", auth=self.auth,
            data={"customer":customer_id,"auto_advance":"false"}, timeout=30)
        inv.raise_for_status()
        return inv.json()
    def payment_status(self, payment_id: str) -> dict:
        r = requests.get(f"{self.base}/invoices/{payment_id}", auth=self.auth, timeout=30)
        r.raise_for_status()
        return r.json()
