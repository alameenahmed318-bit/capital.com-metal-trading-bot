"""Reference backend contract for UAE Market orders.
Do not expose Stripe secret keys in browser code.
Replace this example with a deployed server/serverless function in the next integration step.

POST /api/orders -> create order
POST /api/checkout-session -> create Stripe Checkout Session
GET /api/orders/<id> -> order status
POST /api/webhooks/stripe -> verify Stripe webhook and mark paid
POST /api/webhooks/agent -> notify UAE Business AI Agent
"""
from dataclasses import dataclass

@dataclass
class Order:
    order_id: str
    customer_email: str
    total_aed: int
    status: str = "pending_payment"

# Security requirements:
# - Stripe secret only on server-side environment.
# - Verify Stripe webhook signatures.
# - Recalculate totals server-side from trusted product data.
# - Never trust price/stock values sent by the browser.
# - Use idempotency for order/payment creation.
