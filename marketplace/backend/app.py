"""UAE Market minimal order API.
Run locally with: python -m marketplace.backend.app
For production, deploy behind HTTPS and use a real database.
"""
import json, os, sqlite3, uuid, urllib.parse, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

DB=Path(os.getenv("MARKET_DB","marketplace/backend/market.db"))
DB.parent.mkdir(parents=True,exist_ok=True)\nSTRIPE_SECRET_KEY=os.getenv("STRIPE_SECRET_KEY","")\nSUCCESS_URL=os.getenv("MARKET_SUCCESS_URL","https://example.com/marketplace/success.html")\nCANCEL_URL=os.getenv("MARKET_CANCEL_URL","https://example.com/marketplace/checkout.html")

PRODUCTS={
"P001":{"name":"سماعات لاسلكية Pro","price":129,"stock":24},
"P002":{"name":"ساعة ذكية رياضية","price":199,"stock":18},
"P003":{"name":"حقيبة يومية أنيقة","price":89,"stock":31},
"P004":{"name":"حذاء رياضي خفيف","price":149,"stock":16},
"P005":{"name":"طقم عناية بالبشرة","price":75,"stock":40},
"P006":{"name":"مصباح مكتب ذكي","price":59,"stock":22},
"P007":{"name":"زجاجة ماء حرارية","price":45,"stock":35},
"P008":{"name":"نظارة شمسية عصرية","price":69,"stock":27},
}

def db():
    c=sqlite3.connect(DB)
    c.row_factory=sqlite3.Row
    c.execute("""CREATE TABLE IF NOT EXISTS orders(
      id TEXT PRIMARY KEY, customer_json TEXT NOT NULL, items_json TEXT NOT NULL,
      total INTEGER NOT NULL, currency TEXT NOT NULL, status TEXT NOT NULL,
      created_at TEXT DEFAULT CURRENT_TIMESTAMP)""")
    c.commit()
    return c

class Handler(BaseHTTPRequestHandler):
    def send_json(self,status,payload):
        body=json.dumps(payload,ensure_ascii=False).encode()
        self.send_response(status); self.send_header("Content-Type","application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin","*"); self.send_header("Content-Length",str(len(body))); self.end_headers(); self.wfile.write(body)
    def do_OPTIONS(self):
        self.send_response(204); self.send_header("Access-Control-Allow-Origin","*"); self.send_header("Access-Control-Allow-Headers","Content-Type"); self.send_header("Access-Control-Allow-Methods","GET,POST,OPTIONS"); self.end_headers()
    def do_GET(self):
        if self.path=="/api/health": return self.send_json(200,{"ok":True,"service":"UAE Market API"})
        if self.path=="/api/products": return self.send_json(200,PRODUCTS)\n        if self.path.startswith("/api/checkout/"):\n            oid=self.path.rsplit("/",1)[-1]\n            c=db(); row=c.execute("SELECT * FROM orders WHERE id=?",(oid,)).fetchone(); c.close()\n            if not row: return self.send_json(404,{"error":"not_found"})\n            if row["status"]!="pending_payment": return self.send_json(409,{"error":"order_not_payable","status":row["status"]})\n            customer=json.loads(row["customer_json"]); session=stripe_checkout(oid,row["total"],customer["email"])\n            return self.send_json(200,{"order_id":oid,"checkout_url":session.get("url"),"session_id":session.get("id")})
        if self.path.startswith("/api/orders/"):
            oid=self.path.rsplit("/",1)[-1]
            c=db(); row=c.execute("SELECT * FROM orders WHERE id=?",(oid,)).fetchone(); c.close()
            return self.send_json(200,dict(row) if row else {"error":"not_found"})
        return self.send_json(404,{"error":"not_found"})
    def do_POST(self):
        if self.path!="/api/orders": return self.send_json(404,{"error":"not_found"})
        try:
            n=int(self.headers.get("Content-Length","0")); data=json.loads(self.rfile.read(n))
            customer=data.get("customer") or {}; raw_items=data.get("items") or []
            if not customer.get("email") or not customer.get("name") or not raw_items: raise ValueError("customer and items are required")
            items=[]; total=0
            for x in raw_items:
                p=PRODUCTS.get(x.get("id")); qty=int(x.get("qty",0))
                if not p or qty<1 or qty>p["stock"]: raise ValueError("invalid product or quantity")
                items.append({"id":x["id"],"name":p["name"],"price":p["price"],"qty":qty})
                total+=p["price"]*qty
            oid="UM-"+uuid.uuid4().hex[:10].upper()
            c=db(); c.execute("INSERT INTO orders(id,customer_json,items_json,total,currency,status) VALUES(?,?,?,?,?,?)",
              (oid,json.dumps(customer,ensure_ascii=False),json.dumps(items,ensure_ascii=False),total,"AED","pending_payment")); c.commit(); c.close()
            return self.send_json(201,{"order_id":oid,"total":total,"currency":"AED","status":"pending_payment"})
        except Exception as e: return self.send_json(400,{"error":str(e)})
if __name__=="__main__":
    db().close()
    print("UAE Market API listening on http://127.0.0.1:8080")
    ThreadingHTTPServer(("0.0.0.0",8080),Handler).serve_forever()
