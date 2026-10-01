"""UAE Market order API with Stripe Checkout + signed webhook."""
import hashlib, hmac, json, os, sqlite3, time, urllib.parse, urllib.request, urllib.error, uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

DB=Path(os.getenv("MARKET_DB","marketplace/backend/market.db"))
DB.parent.mkdir(parents=True, exist_ok=True)
STRIPE_SECRET_KEY=os.getenv("STRIPE_SECRET_KEY","")
STRIPE_WEBHOOK_SECRET=os.getenv("STRIPE_WEBHOOK_SECRET","")
SUCCESS_URL=os.getenv("MARKET_SUCCESS_URL","")
CANCEL_URL=os.getenv("MARKET_CANCEL_URL","")
JASANI_API_TOKEN=os.getenv("JASANI_API_TOKEN","")
MARKUP=1.20

PRODUCTS={
"P001":{"name":"سماعات لاسلكية Pro","price":129,"stock":24},"P002":{"name":"ساعة ذكية رياضية","price":199,"stock":18},
"P003":{"name":"حقيبة يومية أنيقة","price":89,"stock":31},"P004":{"name":"حذاء رياضي خفيف","price":149,"stock":16},
"P005":{"name":"طقم عناية بالبشرة","price":75,"stock":40},"P006":{"name":"مصباح مكتب ذكي","price":59,"stock":22},
"P007":{"name":"زجاجة ماء حرارية","price":45,"stock":35},"P008":{"name":"نظارة شمسية عصرية","price":69,"stock":27}}

def supplier_products():
    """Fetch Jasani catalog and apply a 20% markup when a reseller token is configured."""
    if not JASANI_API_TOKEN:
        return {}
    base="https://www.jasani.ae"
    try:
        req=urllib.request.Request(
            f"{base}/products/all/{urllib.parse.quote(JASANI_API_TOKEN)}",
            headers={"Accept":"application/xml"})
        with urllib.request.urlopen(req,timeout=30) as r:
            xml=r.read().decode("utf-8","replace")
        import xml.etree.ElementTree as ET
        root=ET.fromstring(xml)
        out={}
        for node in root.iter():
            if node.tag.split("}")[-1]!="product":
                continue
            def val(name):
                x=node.find(".//"+name)
                return (x.text or "").strip() if x is not None and x.text else ""
            pid=val("id"); name=val("name"); price=val("list_price") or val("price")
            image=val("image_url"); stock=val("net_available_qty") or val("stock")
            if not pid or not name:
                continue
            try: cost=float(price)
            except (TypeError,ValueError): continue
            try: qty=max(0,int(float(stock)))
            except (TypeError,ValueError): qty=0
            out["J"+pid]={"name":name,"cost":round(cost,2),"price":round(cost*MARKUP,2),"stock":qty,"image":image}
        return out
    except Exception as e:
        print("JASANI_SYNC_ERROR:",e)
        return {}

def db():
    c=sqlite3.connect(DB); c.row_factory=sqlite3.Row
    c.execute("""CREATE TABLE IF NOT EXISTS orders(
      id TEXT PRIMARY KEY, customer_json TEXT NOT NULL, items_json TEXT NOT NULL,
      total INTEGER NOT NULL, currency TEXT NOT NULL, status TEXT NOT NULL,
      stripe_session_id TEXT UNIQUE, paid_at TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP)""")
    c.commit(); return c

def stripe_checkout(order_id,total,email):
    if not STRIPE_SECRET_KEY: raise RuntimeError("STRIPE_SECRET_KEY is not configured")
    if not SUCCESS_URL or not CANCEL_URL: raise RuntimeError("MARKET_SUCCESS_URL and MARKET_CANCEL_URL are required")
    data=urllib.parse.urlencode({
      "mode":"payment","success_url":SUCCESS_URL+"?order_id="+urllib.parse.quote(order_id),
      "cancel_url":CANCEL_URL+"?order_id="+urllib.parse.quote(order_id),"customer_email":email,
      "metadata[order_id]":order_id,
      "line_items[0][price_data][currency]":"aed",
      "line_items[0][price_data][product_data][name]":"UAE Market Order "+order_id,
      "line_items[0][price_data][unit_amount]":str(total*100),
      "line_items[0][quantity]":"1"}).encode()
    req=urllib.request.Request("https://api.stripe.com/v1/checkout/sessions",data=data,
      headers={"Authorization":"Bearer "+STRIPE_SECRET_KEY,"Content-Type":"application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(req,timeout=30) as r: return json.loads(r.read())
    except urllib.error.HTTPError as e:
        detail=e.read().decode("utf-8","replace")
        try:
            obj=json.loads(detail); msg=(obj.get("error") or {}).get("message") or detail
        except Exception:
            msg=detail or f"Stripe HTTP {e.code}"
        raise RuntimeError(f"Stripe: {msg}")
    except urllib.error.URLError as e:
        raise RuntimeError(f"Stripe connection: {e.reason}")

def verify_signature(payload,header):
    if not STRIPE_WEBHOOK_SECRET: return False
    timestamp=None; signatures=[]
    for item in header.split(","):
        if "=" not in item: continue
        k,v=item.split("=",1)
        if k=="t": timestamp=v
        elif k=="v1": signatures.append(v)
    if not timestamp or not signatures: return False
    try:
        if abs(time.time()-int(timestamp))>300: return False
    except ValueError: return False
    signed=(timestamp+".").encode()+payload
    expected=hmac.new(STRIPE_WEBHOOK_SECRET.encode(),signed,hashlib.sha256).hexdigest()
    return any(hmac.compare_digest(expected,s) for s in signatures)

def mark_paid(session):
    oid=(session.get("metadata") or {}).get("order_id")
    if not oid or session.get("payment_status")!="paid": return False
    c=db()
    cur=c.execute("""UPDATE orders SET status='paid', stripe_session_id=?, paid_at=CURRENT_TIMESTAMP
                     WHERE id=? AND status!='paid'""",(session.get("id"),oid))
    c.commit(); changed=cur.rowcount>0; c.close()
    return changed

class Handler(BaseHTTPRequestHandler):
    def send_json(self,status,payload):
        body=json.dumps(payload,ensure_ascii=False).encode()
        self.send_response(status); self.send_header("Content-Type","application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin","*"); self.send_header("Content-Length",str(len(body))); self.end_headers(); self.wfile.write(body)
    def do_OPTIONS(self):
        self.send_response(204); self.send_header("Access-Control-Allow-Origin","*")
        self.send_header("Access-Control-Allow-Headers","Content-Type, Stripe-Signature")
        self.send_header("Access-Control-Allow-Methods","GET,POST,OPTIONS"); self.end_headers()
    def do_GET(self):
        if self.path=="/api/health": return self.send_json(200,{"ok":True,"service":"UAE Market API"})
        if self.path=="/api/products":\n            supplier=supplier_products()\n            return self.send_json(200,supplier or PRODUCTS)
        if self.path.startswith("/api/checkout/"):
            oid=self.path.rsplit("/",1)[-1]; c=db(); row=c.execute("SELECT * FROM orders WHERE id=?",(oid,)).fetchone(); c.close()
            if not row: return self.send_json(404,{"error":"not_found"})
            if row["status"]!="pending_payment": return self.send_json(409,{"error":"order_not_payable","status":row["status"]})
            try:
                customer=json.loads(row["customer_json"]); session=stripe_checkout(oid,row["total"],customer["email"])
                c=db(); c.execute("UPDATE orders SET stripe_session_id=? WHERE id=?",(session.get("id"),oid)); c.commit(); c.close()
                return self.send_json(200,{"order_id":oid,"checkout_url":session.get("url"),"session_id":session.get("id")})
            except Exception as e:
                return self.send_json(502,{"error":str(e)})
        if self.path.startswith("/api/orders/"):
            oid=self.path.rsplit("/",1)[-1]; c=db(); row=c.execute("SELECT * FROM orders WHERE id=?",(oid,)).fetchone(); c.close()
            return self.send_json(200,dict(row) if row else {"error":"not_found"})
        return self.send_json(404,{"error":"not_found"})
    def do_POST(self):
        n=int(self.headers.get("Content-Length","0")); raw=self.rfile.read(n)
        if self.path=="/api/orders":
            try:
                data=json.loads(raw); customer=data.get("customer") or {}; raw_items=data.get("items") or []
                if not customer.get("email") or not customer.get("name") or not raw_items: raise ValueError("customer and items are required")
                items=[]; total=0
                for x in raw_items:
                    p=(supplier_products() or PRODUCTS).get(x.get("id")); qty=int(x.get("qty",0))
                    if not p or qty<1 or qty>p["stock"]: raise ValueError("invalid product or quantity")
                    items.append({"id":x["id"],"name":p["name"],"price":p["price"],"qty":qty}); total+=p["price"]*qty
                oid="UM-"+uuid.uuid4().hex[:10].upper(); c=db()
                c.execute("INSERT INTO orders(id,customer_json,items_json,total,currency,status) VALUES(?,?,?,?,?,?)",
                    (oid,json.dumps(customer,ensure_ascii=False),json.dumps(items,ensure_ascii=False),total,"AED","pending_payment"))
                c.commit(); c.close()
                return self.send_json(201,{"order_id":oid,"total":total,"currency":"AED","status":"pending_payment"})
            except Exception as e: return self.send_json(400,{"error":str(e)})
        if self.path=="/api/webhooks/stripe":
            sig=self.headers.get("Stripe-Signature","")
            if not verify_signature(raw,sig): return self.send_json(400,{"error":"invalid_signature"})
            try:
                event=json.loads(raw); event_type=event.get("type")
                if event_type in ("checkout.session.completed","checkout.session.async_payment_succeeded"):
                    mark_paid(event.get("data",{}).get("object",{}))
                return self.send_json(200,{"received":True})
            except Exception as e: return self.send_json(400,{"error":str(e)})
        return self.send_json(404,{"error":"not_found"})

if __name__=="__main__":
    db().close()
    port=int(os.getenv("PORT","10000"))
    print(f"UAE Market API listening on 0.0.0.0:{port}")
    ThreadingHTTPServer(("0.0.0.0",port),Handler).serve_forever()
