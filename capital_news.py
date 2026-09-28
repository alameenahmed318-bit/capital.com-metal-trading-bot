"""Non-blocking Capital.com news cache used by the two active bots."""
import json, os, time, re
import urllib.request
from html.parser import HTMLParser
from urllib.request import Request, urlopen

CACHE_FILE = "capital_news_cache.json"
MAX_AGE_SECONDS = 1800
TIMEOUT_SECONDS = 8
TAVILY_API_URL = "https://api.tavily.com/search"
CAP = "https://" + "capital.com"
SOURCE_URLS = {
    "gold": CAP + "/en-int/analysis/gold-news",
    "forex": CAP + "/en-int/analysis/forex-news",
}

FX = {
 "EUR": ("euro","ecb","european central bank"),
 "GBP": ("sterling","pound","boe","bank of england"),
 "USD": ("dollar","fed","federal reserve","fomc","us treasury"),
 "JPY": ("yen","boj","bank of japan"),
 "CHF": ("franc","snb","swiss national bank"),
 "CAD": ("canadian dollar","loonie","bank of canada","boc"),
 "AUD": ("australian dollar","aussie","rba","reserve bank of australia"),
 "NZD": ("new zealand dollar","kiwi","rbnz","reserve bank of new zealand"),
}
PAIRS = {
 "EURUSD":("EUR","USD"),"GBPUSD":("GBP","USD"),"USDJPY":("USD","JPY"),
 "USDCHF":("USD","CHF"),"USDCAD":("USD","CAD"),"AUDUSD":("AUD","USD"),
 "NZDUSD":("NZD","USD"),"EURGBP":("EUR","GBP"),"EURJPY":("EUR","JPY"),
 "GBPJPY":("GBP","JPY"),"AUDJPY":("AUD","JPY"),"EURCHF":("EUR","CHF"),
 "GBPCHF":("GBP","CHF"),"AUDCAD":("AUD","CAD"),"AUDCHF":("AUD","CHF"),
 "NZDJPY":("NZD","JPY"),"CADJPY":("CAD","JPY"),"EURUSD_W":("EUR","USD")
}
POS=("dovish","rate cut","cuts","easing","lower yields","weaker dollar","weak dollar","rally","rallies","rebound","bullish","safe haven")
NEG=("hawkish","rate hike","hikes","tightening","higher yields","stronger dollar","strong dollar","slides","falls","falling","pressure","bearish","selloff","selling")

class P(HTMLParser):
 def __init__(self): super().__init__(); self.on=False; self.href=""; self.txt=[]; self.items=[]
 def handle_starttag(self,t,a):
  if t=="a":
   h=dict(a).get("href","")
   if "/analysis/" in h: self.on=True; self.href=h; self.txt=[]
 def handle_data(self,d):
  if self.on: self.txt.append(d)
 def handle_endtag(self,t):
  if t=="a" and self.on:
   s=re.sub(r"\s+"," "," ".join(self.txt)).strip()
   if 15<=len(s)<=240: self.items.append((self.href,s))
   self.on=False

def _tavily_items():
 """Get fresh market/news context from Tavily when configured."""
 key=os.getenv("TAVILY_API_KEY")
 if not key: return [], []
 queries=[
  ("gold","gold XAUUSD latest market news Federal Reserve yields dollar"),
  ("forex","forex latest market news Federal Reserve ECB BOE BOJ RBA currencies"),
 ]
 out=[]; errors=[]
 for category,query in queries:
  try:
   body=json.dumps({"api_key":key,"query":query,"topic":"news","search_depth":"basic","max_results":6,"include_answer":False,"include_raw_content":False}).encode("utf-8")
   req=urllib.request.Request(TAVILY_API_URL,data=body,headers={"Content-Type":"application/json","User-Agent":"Capital-AI-Bot/1.0"},method="POST")
   with urllib.request.urlopen(req,timeout=TIMEOUT_SECONDS) as resp: data=json.loads(resp.read().decode("utf-8","ignore"))
   for item in data.get("results",[]) or []:
    title=str(item.get("title") or "").strip(); url=str(item.get("url") or "").strip()
    blob=(title+" "+str(item.get("content") or "")).lower()
    if not title: continue
    pos=sum(x in blob for x in POS); neg=sum(x in blob for x in NEG)
    sentiment="BULLISH" if pos>neg else "BEARISH" if neg>pos else "NEUTRAL"
    strength=min(1.0,0.35+0.15*abs(pos-neg)) if pos!=neg else 0.0
    out.append({"category":category,"title":title,"url":url,"sentiment":sentiment,"strength":strength,"source":"TAVILY"})
  except Exception as e: errors.append(category+": "+str(e))
 return out,errors

def refresh():
 items=[]; errors=[]
 tavily_items,tavily_errors=_tavily_items()
 items.extend(tavily_items)
 errors.extend(["tavily: "+str(x) for x in tavily_errors])
 for category,url in SOURCE_URLS.items():
  try:
   req=Request(url,headers={"User-Agent":"Mozilla/5.0"})
   html=urlopen(req,timeout=TIMEOUT_SECONDS).read().decode("utf-8","ignore")
   p=P(); p.feed(html); seen=set()
   for href,title in p.items:
    k=title.lower()
    if k in seen: continue
    seen.add(k); t=k
    pos=sum(x in t for x in POS); neg=sum(x in t for x in NEG)
    sentiment="BULLISH" if pos>neg else "BEARISH" if neg>pos else "NEUTRAL"
    strength=min(1.0,0.35+0.15*abs(pos-neg)) if pos!=neg else 0.0
    items.append({"category":category,"title":title,"url":href,"sentiment":sentiment,"strength":strength})
  except Exception as e: errors.append(category+": "+str(e))
 payload={"fetched_at":time.time(),"errors":errors,"items":items}
 tmp=CACHE_FILE+".tmp"
 with open(tmp,"w",encoding="utf-8") as f: json.dump(payload,f,ensure_ascii=False,indent=2)
 os.replace(tmp,CACHE_FILE)
 print("CAPITAL NEWS CACHE | items=%d | errors=%s"%(len(items),errors))
 return payload

def score(epic,direction):
 try:
  with open(CACHE_FILE,"r",encoding="utf-8") as f: d=json.load(f)
  if time.time()-float(d.get("fetched_at",0))>MAX_AGE_SECONDS: return 0.0,[]
 except Exception: return 0.0,[]
 eu=str(epic).upper()
 pair=PAIRS.get(eu)
 rows=[]
 for x in d.get("items",[]):
  title=str(x.get("title","")); tl=title.lower()
  if eu=="GOLD": ok=x.get("category")=="gold"
  elif pair: ok=x.get("category")=="forex" and any(k in tl for c in pair for k in FX.get(c,()))
  else: ok=False
  if ok: rows.append(x)
 signed=0.0; reasons=[]
 for x in rows[:12]:
  s=float(x.get("strength",0) or 0)
  if x.get("sentiment")=="BULLISH": signed+=s
  elif x.get("sentiment")=="BEARISH": signed-=s
  if x.get("sentiment") in ("BULLISH","BEARISH"): reasons.append(x.get("sentiment")+":"+str(x.get("title",""))[:80])
 signed=max(-3.0,min(3.0,signed*0.35))
 bonus=signed if direction=="BUY" else -signed
 return max(-3.0,min(3.0,bonus)),reasons[:4]

if __name__=="__main__": refresh()
