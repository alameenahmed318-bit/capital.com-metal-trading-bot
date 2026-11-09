"""Central copy of the original AL-ZOOL 200 composite strategy engine."""
from __future__ import annotations
from functools import lru_cache
import random

FAMILIES=[
("EMA Trend","trend"),("RSI Momentum","rsi"),("MACD Momentum","macd"),
("Bollinger Reversion","bb"),("Donchian Breakout","breakout"),("ATR Expansion","atr"),
("Candle Momentum","candle"),("EMA Pullback","pullback"),("Range Reversion","range"),
("Hybrid Confluence","hybrid"),("Stochastic Reversal","stoch"),("CCI Momentum","cci"),
("Williams %R Reversal","williams"),("ROC Momentum","roc"),("Adaptive Swing Trend","parabolic"),
("VWAP Deviation","vwap"),("Pivot Breakout","pivot"),("Inside Bar Breakout","inside"),
("Engulfing Reversal","engulf"),("Volume Pressure","volume")]
GATES=[
("Candle Direction","candle"),("EMA Slope","ema_slope"),("RSI Momentum Zone","rsi_zone"),
("Volume Confirmation","volume"),("Range Expansion","range"),("Close Location","close_location"),
("Short Momentum","momentum"),("VWAP Side","vwap"),("Micro Breakout","micro_breakout"),
("Wick Rejection","wick")]
STRATEGIES=[]
for fi,(family,base_kind) in enumerate(FAMILIES):
    for gi,(gate_name,gate_kind) in enumerate(GATES):
        STRATEGIES.append({"id":len(STRATEGIES)+1,"name":f"{family} + {gate_name}",
        "family":family,"kind":"composite","base_kind":base_kind,"gate_kind":gate_kind,
        "fast":5+((fi*3+gi)%12),"slow":18+((fi*7+gi*2)%35),
        "period":7+((fi+gi*2)%18),"threshold":45+((fi*2+gi*3)%20),
        "variant":1+((fi+gi)%10),"base_family_id":fi+1,"confirmation_id":gi+1})
def _build_combinations():
    rng=random.Random(261102); by_family={}
    for s in STRATEGIES: by_family.setdefault(s["family"],[]).append(s["id"])
    families=sorted(by_family); out=[]; seen=set()
    while len(out)<1000:
        chosen=rng.sample(families,5); members=tuple(sorted(rng.choice(by_family[f]) for f in chosen))
        if members in seen: continue
        seen.add(members); out.append({"id":len(out)+1,"name":f"AZ Combination {len(out)+1:04d}","members":members})
    return out
COMBINATIONS=_build_combinations()
assert len(STRATEGIES)==200 and len({(s["base_kind"],s["gate_kind"]) for s in STRATEGIES})==200
assert len(COMBINATIONS)==1000 and len({c["members"] for c in COMBINATIONS})==1000

@lru_cache(maxsize=8192)
def _ema_cached(values,period):
    if len(values)<period:return None
    alpha=2.0/(period+1.0); out=values[0]
    for v in values[1:]:out=alpha*v+(1-alpha)*out
    return out
def ema(values,period):return _ema_cached(tuple(float(v) for v in values),int(period))
@lru_cache(maxsize=8192)
def _rsi_cached(closes,period):
    if len(closes)<period+1:return None
    changes=[b-a for a,b in zip(closes[-period-1:-1],closes[-period:])]
    gain=sum(max(x,0) for x in changes)/period; loss=sum(max(-x,0) for x in changes)/period
    return 100.0 if loss==0 else 100-100/(1+gain/loss)
def rsi(closes,period=14):return _rsi_cached(tuple(float(v) for v in closes),int(period))

def _base(rates,s):
    if len(rates)<60:return "WAIT","بيانات الشموع غير كافية"
    c=[float(x["close"]) for x in rates]; h=[float(x["high"]) for x in rates]
    l=[float(x["low"]) for x in rates]; o=[float(x["open"]) for x in rates]
    v=int(s["variant"]); kind=s["kind"]; close=c[-1]
    fast=ema(c[-(s["fast"]*4):],s["fast"]); slow=ema(c[-(s["slow"]*4):],s["slow"])
    mom=rsi(c,s["period"]); rh=max(h[-(10+v):-1]); rl=min(l[-(10+v):-1])
    ar=sum(a-b for a,b in zip(h[-(14+v):-1],l[-(14+v):-1]))/(14+v)
    bull=close>o[-1]; bear=close<o[-1]; buy=sell=False; reason="لا توجد إشارة مؤكدة"
    if kind=="trend": buy,sell=fast>slow and close>fast,fast<slow and close<fast; reason="EMA trend alignment"
    elif kind=="rsi": buy,sell=mom is not None and mom>50+v,mom is not None and mom<50-v; reason=f"RSI momentum ({mom:.1f})" if mom is not None else reason
    elif kind=="macd":
        a,b=ema(c,12+v),ema(c,26+v); buy,sell=a is not None and b is not None and a>b,a is not None and b is not None and a<b; reason="MACD-style EMA momentum"
    elif kind=="bb":
        w=c[-(20+v):]; mean=sum(w)/len(w); sd=(sum((x-mean)**2 for x in w)/len(w))**.5
        buy,sell=close<mean-sd*(1.2+v*.05),close>mean+sd*(1.2+v*.05); reason="Bollinger-style mean reversion"
    elif kind=="breakout":buy,sell=close>rh,close<rl; reason="Closed-candle range breakout"
    elif kind=="atr":
        body=abs(close-o[-1]); buy,sell=body>ar*(.7+v*.08) and bull,body>ar*(.7+v*.08) and bear; reason="Range expansion candle"
    elif kind=="candle":
        prev=abs(c[-2]-o[-2]); body=abs(close-o[-1]); buy,sell=bull and body>prev*(1+v*.05),bear and body>prev*(1+v*.05); reason="Candle body momentum"
    elif kind=="pullback":buy,sell=fast>slow and close<fast and bull,fast<slow and close>fast and bear; reason="Trend pullback and candle confirmation"
    elif kind=="range":
        w=c[-(20+v):]; mean=sum(w)/len(w); buy,sell=close<mean-ar*(.5+v*.1),close>mean+ar*(.5+v*.1); reason="Range deviation reversion"
    elif kind=="hybrid":buy,sell=fast>slow and mom is not None and mom>52+v and bull,fast<slow and mom is not None and mom<48-v and bear; reason="EMA + RSI + candle confluence"
    elif kind=="stoch":
        hh,ll=max(h[-(14+v):-1]),min(l[-(14+v):-1]); k=100*(close-ll)/max(hh-ll,1e-9); buy,sell=k<25+v and bull,k>75-v and bear; reason=f"Stochastic reversal ({k:.1f})"
    elif kind=="cci":
        tp=[(a+b+d)/3 for a,b,d in zip(h,l,c)]; w=tp[-(14+v):]; mean=sum(w)/len(w); md=sum(abs(x-mean) for x in w)/len(w); z=(tp[-1]-mean)/(.015*md) if md else 0; buy,sell=z>70+v*3,z<-(70+v*3); reason=f"CCI directional momentum ({z:.1f})"
    elif kind=="williams":
        hh,ll=max(h[-(14+v):-1]),min(l[-(14+v):-1]); z=-100*(hh-close)/max(hh-ll,1e-9); buy,sell=z<(-80+v) and bull,z>(-20-v) and bear; reason=f"Williams %R reversal ({z:.1f})"
    elif kind=="roc":
        base=c[-(10+v)] if len(c)>10+v else close; z=100*(close-base)/max(abs(base),1e-9); buy,sell=z>.03*v,z<-.03*v; reason=f"Rate of change momentum ({z:.3f}%)"
    elif kind=="parabolic":
        lo,hi=min(l[-(8+v):-1]),max(h[-(8+v):-1]); buy,sell=fast>slow and close>lo+(hi-lo)*.62,fast<slow and close<hi-(hi-lo)*.62; reason="Adaptive swing-trend continuation"
    elif kind=="vwap":
        sample=rates[-(20+v):]; vol=[max(1.,float(x.get("tick_volume",1))) for x in sample]; px=[(float(x["high"])+float(x["low"])+float(x["close"]))/3 for x in sample]; vw=sum(a*b for a,b in zip(px,vol))/sum(vol); buy,sell=close>vw+ar*.05*v,close<vw-ar*.05*v; reason="Tick-volume weighted price pressure"
    elif kind=="pivot":
        pivot=(h[-2]+l[-2]+c[-2])/3; r1=2*pivot-l[-2]; s1=2*pivot-h[-2]; buy,sell=close>r1 and bull,close<s1 and bear; reason="Previous-session pivot level breakout"
    elif kind=="inside":
        inside=h[-1]<h[-2] and l[-1]>l[-2]; buy=inside and close>o[-1] and close>(h[-2]+l[-2])/2; sell=inside and close<o[-1] and close<(h[-2]+l[-2])/2; reason="Inside-bar compression and directional close"
    elif kind=="engulf":
        buy=c[-2]<o[-2] and close>o[-1] and o[-1]<=c[-2] and close>=o[-2]; sell=c[-2]>o[-2] and close<o[-1] and o[-1]>=c[-2] and close<=o[-2]; reason="Two-candle engulfing reversal pattern"
    elif kind=="volume":
        vols=[float(x.get("tick_volume",0)) for x in rates[-(20+v):-1]]; pressure=float(rates[-1].get("tick_volume",0))/max(sum(vols)/max(1,len(vols)),1); buy,sell=pressure>1.15+v*.04 and bull,pressure>1.15+v*.04 and bear; reason=f"Tick-volume expansion ({pressure:.2f}x)"
    return ("BUY",reason) if buy and not sell else ("SELL",reason) if sell and not buy else ("WAIT",reason)

def _confirm(rates,s):
    c=[float(x["close"]) for x in rates]; h=[float(x["high"]) for x in rates]; l=[float(x["low"]) for x in rates]; o=[float(x["open"]) for x in rates]
    vols=[max(0.,float(x.get("tick_volume",0))) for x in rates]; gate=s["gate_kind"]; op,hi,lo,cl=o[-1],h[-1],l[-1],c[-1]; buy=sell=False
    if gate=="candle":buy,sell=cl>op,cl<op
    elif gate=="ema_slope":
        a,b=ema(c[-80:],s["fast"]),ema(c[-90:-10],s["fast"]); buy,sell=a is not None and b is not None and a>b,a is not None and b is not None and a<b
    elif gate=="rsi_zone":
        value=rsi(c,s["period"]); t=s["threshold"]; buy,sell=value is not None and value>t,value is not None and value<100-t
    elif gate=="volume":
        avg=sum(vols[-21:-1])/max(1,len(vols[-21:-1])); buy,sell=vols[-1]>avg*1.15 and cl>op,vols[-1]>avg*1.15 and cl<op
    elif gate=="range":
        avg=sum(h[i]-l[i] for i in range(-15,-1))/14; buy,sell=hi-lo>avg*1.2 and cl>op,hi-lo>avg*1.2 and cl<op
    elif gate=="close_location":
        pos=(cl-lo)/max(hi-lo,1e-12); buy,sell=pos>=.70,pos<=.30
    elif gate=="momentum":
        delta=cl-c[-1-(s["variant"]+2)]; buy,sell=delta>0,delta<0
    elif gate=="vwap":
        sample=rates[-20:]; vv=[max(1.,float(x.get("tick_volume",1))) for x in sample]; vw=sum(((float(x["high"])+float(x["low"])+float(x["close"]))/3)*v for x,v in zip(sample,vv))/sum(vv); buy,sell=cl>vw,cl<vw
    elif gate=="micro_breakout":buy,sell=cl>max(h[-6:-1]),cl<min(l[-6:-1])
    elif gate=="wick":
        span=max(hi-lo,1e-12); buy,sell=(min(op,cl)-lo)/span>=.45 and cl>op,(hi-max(op,cl))/span>=.45 and cl<op
    label=GATES[s["confirmation_id"]-1][0]
    return ("BUY",f"تأكيد مستقل: {label}") if buy and not sell else ("SELL",f"تأكيد مستقل: {label}") if sell and not buy else ("WAIT",f"لم يتحقق تأكيد {label}")

def _arabic_trigger_reason(reason):
    exact={"EMA trend alignment":"المتوسطات المتحركة تشير إلى اتجاه السوق.",
    "MACD-style EMA momentum":"زخم المتوسطات المتحركة يحدد اتجاه الإشارة.",
    "Bollinger-style mean reversion":"السعر ابتعد عن متوسط النطاق وقد يعود نحوه.",
    "Closed-candle range breakout":"إغلاق الشمعة كسر نطاق الشموع السابقة.",
    "Range expansion candle":"مدى الشمعة أكبر من المعتاد، مع فحص اتجاهها.",
    "Candle body momentum":"جسم الشمعة الحالية أقوى من الشمعة السابقة وفي اتجاهها.",
    "Trend pullback and candle confirmation":"السعر في تراجع مؤقت داخل الاتجاه مع تأكيد الشمعة.",
    "Range deviation reversion":"السعر ابتعد عن متوسط النطاق.",
    "EMA + RSI + candle confluence":"توافق اتجاه المتوسطات والزخم واتجاه الشمعة."}
    if reason in exact:return exact[reason]
    if reason.startswith("RSI momentum ("):return "مؤشر القوة النسبية RSI يوضح زخم الحركة: "+reason.split("(",1)[1].rstrip(")")
    return reason

def calculate_signal(rates,s):
    if len(rates)<60:return "WAIT","بيانات الشموع غير كافية"
    base=dict(s); base["kind"]=s["base_kind"]
    trigger,why=_base(rates,base)
    if trigger=="WAIT":return "WAIT",f"المحفّز لم يكتمل: {_arabic_trigger_reason(why)}"
    confirm,gatewhy=_confirm(rates,s)
    return (trigger,f"المحفّز: {_arabic_trigger_reason(why)}؛ {gatewhy}") if confirm==trigger else ("WAIT",f"إشارة المحفّز {trigger} دون تأكيد مستقل؛ {gatewhy}")

def analyze_market_context(rates):
    if len(rates)<65:return {"regime":"غير محدد","atr":0.,"atr_ratio":1.,"trend_strength":0.,"adx":0.,"vwap":None,"buy_quality":0.,"sell_quality":0.,"buy_trap":False,"sell_trap":False,"support":None,"resistance":None,"reason":"بيانات غير كافية"}
    c=[float(x["close"]) for x in rates]; h=[float(x["high"]) for x in rates]; l=[float(x["low"]) for x in rates]; o=[float(x["open"]) for x in rates]
    tr=[max(h[i]-l[i],abs(h[i]-c[i-1]),abs(l[i]-c[i-1])) for i in range(1,len(rates))]
    atr=sum(tr[-14:])/14; base=sum(tr[-60:-14])/46; ratio=atr/base if base>0 else 1.
    e20,e50=ema(c[-100:],20),ema(c[-100:],50); old=ema(c[-30:-10],20); slope=(e20-old)/atr if e20 and old and atr>0 else 0.
    strength=min(1.,abs(slope)/1.5) if e20 and e50 else 0.
    regime="تقلب مرتفع" if ratio>=1.7 else ("اتجاه صاعد" if e20 and e50 and e20>e50 and abs(e20-e50)>atr*.65 and strength>.20 else "اتجاه هابط" if e20 and e50 and e20<e50 and abs(e20-e50)>atr*.65 and strength>.20 else "تذبذب جانبي")
    op,hi,lo,cl=o[-1],h[-1],l[-1],c[-1]; span=max(hi-lo,1e-9); pos=(cl-lo)/span; body=abs(cl-op)
    uw=(hi-max(op,cl))/span; lw=(min(op,cl)-lo)/span; ph=max(h[-21:-1]); pl=min(l[-21:-1])
    buy_trap=hi>ph and cl<ph and uw>.35; sell_trap=lo<pl and cl>pl and lw>.35; mom=rsi(c,14) or 50.
    plus=[]; minus=[]
    for i in range(max(1,len(rates)-14),len(rates)):
        up=h[i]-h[i-1]; down=l[i-1]-l[i]; plus.append(max(up,0.) if up>down else 0.); minus.append(max(down,0.) if down>up else 0.)
    tr14=max(sum(tr[-14:]),1e-9); pdi=100*sum(plus)/tr14; mdi=100*sum(minus)/tr14; adx=100*abs(pdi-mdi)/max(pdi+mdi,1e-9)
    vw=None
    if "tick_volume" in rates[-1]:
        sample=rates[-30:]; vv=[max(1.,float(x.get("tick_volume",1))) for x in sample]; typical=[(float(x["high"])+float(x["low"])+float(x["close"]))/3 for x in sample]; vw=sum(a*b for a,b in zip(typical,vv))/sum(vv)
    db=1. if e20 and e50 and e20>e50 else 0.; ds=1. if e20 and e50 and e20<e50 else 0.
    cb=max(0.,min(1.,(pos-.35)/.65))*min(1.,body/max(atr*.25,1e-9)); cs=max(0.,min(1.,(.65-pos)/.65))*min(1.,body/max(atr*.25,1e-9))
    mb=max(0.,min(1.,(mom-45)/25)); ms=max(0.,min(1.,(55-mom)/25))
    tb=.45*db+.35*max(0.,min(1.,.5+slope/2))+.20*min(1.,adx/35); ts=.45*ds+.35*max(0.,min(1.,.5-slope/2))+.20*min(1.,adx/35)
    if vw is not None:mb=.85*mb+.15*(1. if cl>=vw else 0.);ms=.85*ms+.15*(1. if cl<=vw else 0.)
    penalty=.15 if ratio>2.2 else 0.
    bq=max(0.,min(100.,100*(.42*tb+.33*mb+.25*cb)-penalty*100-(25 if buy_trap else 0)))
    sq=max(0.,min(100.,100*(.42*ts+.33*ms+.25*cs)-penalty*100-(25 if sell_trap else 0)))
    return {"regime":regime,"atr":atr,"atr_ratio":ratio,"trend_strength":strength,"adx":adx,"vwap":vw,"buy_quality":bq,"sell_quality":sq,"buy_trap":buy_trap,"sell_trap":sell_trap,"support":min(l[-40:-1]),"resistance":max(h[-40:-1]),"reason":f"ATR ratio={ratio:.2f}; RSI={mom:.1f}; ADX={adx:.1f}; trend strength={strength:.2f}; candle close position={pos:.2f}"}

def adaptive_gold_parameters(rates,context=None):
    context=context or analyze_market_context(rates); ratio=float(context.get("atr_ratio",1.) or 1.); regime=context.get("regime","غير محدد")
    if ratio>=1.8: rp,ef,es,mode=9,8,21,"استجابة أسرع للتقلب المرتفع"
    elif regime.startswith("اتجاه"):rp,ef,es,mode=14,12,30,"تأكيد الاتجاه وتقليل ضوضاء الحركة"
    elif regime=="تذبذب جانبي":rp,ef,es,mode=18,9,26,"تنعيم الإشارات داخل النطاق"
    else:rp,ef,es,mode=14,10,26,"إعداد متوازن عند نقص وضوح الحالة"
    if ratio<.65:rp=min(21,rp+2);es=min(40,es+4);mode+="؛ تقلب منخفض"
    return {"rsi_period":rp,"ema_fast":ef,"ema_slow":es,"atr_ratio":ratio,"regime":regime,"mode":mode}

def build_ai_council(rates,votes=None,context=None,adaptive=None):
    context=context or analyze_market_context(rates); votes=votes or []
    buy=sum(x["signal"]=="BUY" for x in votes); sell=sum(x["signal"]=="SELL" for x in votes); wait=sum(x["signal"]=="WAIT" for x in votes)
    regime=context.get("regime","غير محدد")
    trap="فخ شراء محتمل" if context.get("buy_trap") else "فخ بيع محتمل" if context.get("sell_trap") else "لا يظهر فخ اختراق واضح"
    direction="BUY" if buy>sell and context.get("buy_quality",0)>=50 and not context.get("buy_trap") else "SELL" if sell>buy and context.get("sell_quality",0)>=50 and not context.get("sell_trap") else "WAIT"
    seats=[("محلل الاتجاه",regime,f"قوة الاتجاه {context.get('trend_strength',0):.2f} | ADX {context.get('adx',0):.1f}"),
    ("محلل السيولة والفخاخ",trap,f"الدعم {context.get('support') or 0:.3f} | المقاومة {context.get('resistance') or 0:.3f}"),
    ("محلل الشموع",f"جودة شراء {context.get('buy_quality',0):.0f}/100 | جودة بيع {context.get('sell_quality',0):.0f}/100","تقييم موقع الإغلاق والفتائل وحجم جسم الشمعة"),
    ("محلل الاستراتيجيات",f"شراء {buy} | بيع {sell} | انتظار {wait}","تصويت الاستراتيجيات المحسوب من آخر شموع مغلقة"),
    ("محلل المخاطر","تقلب مرتفع؛ خفّض الثقة وراجع حجم المخاطرة" if context.get("atr_ratio",1)>=1.8 else "مخاطر الحركة ضمن النطاق المرصود",f"نسبة ATR {context.get('atr_ratio',1):.2f}"),
    ("القرار النهائي",{"BUY":"ميل للشراء","SELL":"ميل للبيع","WAIT":"انتظار وعدم استعجال"}[direction],"قرار تحليلي؛ لا يرسل أمراً")]
    return {"direction":direction,"seats":seats,"buy":buy,"sell":sell,"wait":wait}

def evaluate_market(rates,weights=None,mtf_bias=None,mode='AUTO',selected_strategy_id=0):
    weights=weights or {}; mtf_bias=mtf_bias or {}; context=analyze_market_context(rates); adaptive=adaptive_gold_parameters(rates,context)
    votes=[]; counts={"BUY":0,"SELL":0,"WAIT":0}; candidates=[]
    for s in STRATEGIES:
        tuned=dict(s); tuned["fast"]=max(5,min(30,adaptive["ema_fast"]+s["fast"]-10)); tuned["slow"]=max(tuned["fast"]+3,min(60,adaptive["ema_slow"]+s["slow"]-26)); tuned["period"]=max(5,min(30,adaptive["rsi_period"]+s["period"]-14))
        sig,reason=calculate_signal(rates,tuned); weight=float(weights.get(str(s["id"]),1.)); fam=s["family"]; bonus=1.
        if context["regime"].startswith("اتجاه") and fam in ("EMA Trend","EMA Pullback","Hybrid Confluence","MACD Momentum"):bonus=1.2
        elif context["regime"]=="تذبذب جانبي" and fam in ("Bollinger Reversion","Range Reversion","RSI Momentum"):bonus=1.2
        elif context["regime"]=="تقلب مرتفع" and fam in ("Donchian Breakout","ATR Expansion","Candle Momentum"):bonus=1.15
        aligned=sum(b==sig for b in mtf_bias.values()) if sig!="WAIT" else 0; opposed=sum(b in ("BUY","SELL") and b!=sig for b in mtf_bias.values()) if sig!="WAIT" else 0
        score=weight*bonus*(1+.06*aligned-.04*opposed); counts[sig]+=1
        votes.append({"id":s["id"],"family":fam,"name":s["name"],"signal":sig,"weight":weight,"adjusted_score":score,"reason":reason})
        if sig in ("BUY","SELL"):candidates.append((score,s,sig,reason))
    byid={x["id"]:x for x in votes}; combo_votes=[]; cc={"BUY":0,"SELL":0,"WAIT":0}
    for combo in COMBINATIONS:
        members=[byid[i] for i in combo["members"]]; directional={d:sum(max(.25,m["weight"]) for m in members if m["signal"]==d) for d in ("BUY","SELL")}; active=sum(m["signal"] in ("BUY","SELL") for m in members); total=sum(directional.values())
        if active<2 or total<=0:cs,conf,why="WAIT",0.,"أقل من عضوين لديهما إشارة اتجاهية"
        else:
            win="BUY" if directional["BUY"]>directional["SELL"] else "SELL" if directional["SELL"]>directional["BUY"] else "WAIT"; conf=max(directional.values())/total
            if win=="WAIT" or conf<.55:cs,why="WAIT","تضارب الأصوات أو توافق أقل من 55%"
            else:cs,why=win,f"{sum(m['signal']==win for m in members)} من 5 أعضاء يؤيدون الاتجاه"
        cc[cs]+=1; combo_votes.append({"id":combo["id"],"name":combo["name"],"members":" + ".join(m["name"] for m in members),"member_ids":list(combo["members"]),"signal":cs,"confidence":conf,"reason":why})
    fs={f:{"BUY":0.,"SELL":0.} for f,_ in FAMILIES}
    for v in votes:
        if v["signal"] in ("BUY","SELL"):fs[v["family"]][v["signal"]]+=max(.01,v["adjusted_score"])
    fv={}
    for f,x in fs.items():
        total=x["BUY"]+x["SELL"];fv[f]="WAIT" if total<=0 or abs(x["BUY"]-x["SELL"])/total<.10 else "BUY" if x["BUY"]>x["SELL"] else "SELL"
    fb=sum(x=="BUY" for x in fv.values());fsell=sum(x=="SELL" for x in fv.values());active=fb+fsell; bshare=fb/max(1,active); sshare=fsell/max(1,active)
    direction="BUY" if bshare>sshare else "SELL" if sshare>bshare else "WAIT"; famagree=max(bshare,sshare); dc=cc["BUY"]+cc["SELL"]; combo_share=cc[direction]/max(1,dc) if direction in ("BUY","SELL") else 0.
    agreement=.85*famagree+.15*combo_share;quality=context["buy_quality"] if direction=="BUY" else context["sell_quality"] if direction=="SELL" else 0.;trap=context["buy_trap"] if direction=="BUY" else context["sell_trap"] if direction=="SELL" else False
    aligned=sum(b==direction for b in mtf_bias.values());opposed=sum(b in ("BUY","SELL") and b!=direction for b in mtf_bias.values());breadth=fb if direction=="BUY" else fsell if direction=="SELL" else 0
    if str(mode).startswith("HUNTER"): min_ag,min_breadth,min_combo,min_quality,min_final,policy=.50,2,.42,42,.48,"HUNTER"
    elif str(mode).startswith("SNIPER"): min_ag,min_breadth,min_combo,min_quality,min_final,policy=.65,4,.58,65,.64,"SNIPER"
    else: min_ag,min_breadth,min_combo,min_quality,min_final,policy=.60,3,.50,50,.56,"AUTO"
    if direction=="WAIT" or active<min_breadth or famagree<min_ag:final="WAIT";reason=f"{policy}: انتظار توافق العائلات؛ شراء {fb}/20، بيع {fsell}/20، التوافق {famagree:.0%} (المطلوب {min_ag:.0%})"
    elif breadth<min_breadth:final="WAIT";reason=f"{policy}: عدد العائلات المؤيدة {breadth}؛ المطلوب {min_breadth}"
    elif combo_share<min_combo:final="WAIT";reason=f"{policy}: توافق التركيبات {combo_share:.0%}؛ المطلوب {min_combo:.0%}"
    elif quality<min_quality:final="WAIT";reason=f"{policy}: جودة نقطة الدخول {quality:.0f}/100؛ المطلوب {min_quality}"
    else:
        if trap:agreement-=.03
        if opposed>aligned and len(mtf_bias)>=3:agreement-=.04
        if agreement<min_final:final="WAIT";reason=f"{policy}: الثقة بعد مراجعة ظروف السوق {agreement:.0%}؛ المطلوب {min_final:.0%}"
        else:
            pool=[x for x in candidates if x[2]==direction];pool.sort(key=lambda x:(x[0],float(weights.get(str(x[1]["id"]),1.))),reverse=True)
            if pool:_,chosen,final,why=pool[0];reason=f"{policy} | AZ MASTER BRAIN: توافق العائلات {famagree:.0%}؛ العائلات المؤيدة {breadth}/20؛ توافق التركيبات {combo_share:.0%}؛ الثقة {agreement:.0%}؛ الجودة {quality:.0f}/100؛ الفريمات {aligned}/{len(mtf_bias)}؛ السوق {context['regime']}؛ {context['reason']}؛ {why}"
            else:final="WAIT";chosen=None;reason="لا توجد استراتيجية أصلية متوافقة"
    if selected_strategy_id:
        selected_id=int(selected_strategy_id)
        if not 1<=selected_id<=len(STRATEGIES): raise ValueError("selected_strategy_id must be 1..200")
        selected=STRATEGIES[selected_id-1]
        selected_vote=next(v for v in votes if v["id"]==selected_id)
        selected_signal=selected_vote["signal"]
        selected_quality=context["buy_quality"] if selected_signal=="BUY" else context["sell_quality"] if selected_signal=="SELL" else 0.
        selected_trap=context["buy_trap"] if selected_signal=="BUY" else context["sell_trap"] if selected_signal=="SELL" else False
        if selected_signal in ("BUY","SELL") and selected_trap:
            final="WAIT"; reason=f"الاستراتيجية المحددة {selected_id}: تم منع الدخول بسبب احتمال فخ سعري"
        elif selected_signal in ("BUY","SELL"):
            final=selected_signal; chosen=next((x for x in candidates if x[1]["id"]==selected_id),None)
            if chosen is None: final="WAIT"; reason=f"الاستراتيجية المحددة {selected_id}: لا توجد إشارة تنفيذ صالحة"
            else:
                _,chosen,_,why=chosen
                agreement=max(0.,min(1.,float(selected_vote["adjusted_score"])/1.5))
                reason=f"الاستراتيجية المحددة #{selected_id} — {selected['name']}؛ {why}؛ جودة نقطة الدخول {selected_quality:.0f}/100؛ السوق {context['regime']}"
        else:
            final="WAIT"; reason=f"الاستراتيجية المحددة #{selected_id} — {selected['name']}: لا توجد إشارة مؤكدة على الشمعة المغلقة"
            chosen=selected
    if "chosen" not in locals():chosen=None
    sid=int(chosen["id"]) if chosen else 0; name=chosen["name"] if chosen else "AZ MASTER BRAIN — 1000 COMBINATIONS"
    council=build_ai_council(rates,votes,context,adaptive);council["direction"]=final
    council["seats"][-1]=(council["seats"][-1][0],{"BUY":"ميل للشراء","SELL":"ميل للبيع","WAIT":"انتظار وعدم استعجال"}[final],"القرار النهائي بعد سياسة AUTO وضوابط السوق")
    return {"decision":final,"strategy_id":sid,"strategy_name":name,"reason_ar":reason,"confidence":max(0.,min(1.,agreement)),"context":context,"adaptive":adaptive,"council":council,"votes":votes,"vote_summary":counts,"combination_votes":combo_votes,"combination_summary":cc,"family_votes":fv,"mtf_bias":mtf_bias,"strategy_count":200,"combination_count":1000}
