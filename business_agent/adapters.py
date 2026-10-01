"""Safe adapter interfaces.

Real providers are connected later. The agent never gets raw payment credentials
or unrestricted messaging access in this layer.
"""

from typing import Protocol

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
