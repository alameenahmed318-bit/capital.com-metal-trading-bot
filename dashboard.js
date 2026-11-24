let supabaseClient=null,accessToken=null,mt5Connected=false,autoTradingBusy=false;
let activeMarketSymbol=window.ALZOOL_MARKET_VIEW?.()||"BTCUSD";
function setMarketChart(symbol){
 const value=String(symbol||window.ALZOOL_MARKET_VIEW?.()||"BTCUSD").toUpperCase();
 if(!/^(BTCUSD|XAUUSD)/.test(value))return;
 activeMarketSymbol=value;
 if(document.getElementById("marketChartTitle"))document.getElementById("marketChartTitle").textContent=value+" · M1";
 if(document.getElementById("shadowMarketSymbol"))document.getElementById("shadowMarketSymbol").textContent=value;
}
const $=id=>document.getElementById(id);
async function api(path,options={}){
 const headers={...(accessToken?{Authorization:"Bearer "+accessToken}:{}),...(options.headers||{})};
 const r=await fetch(path,{...options,headers});
 const d=await r.json().catch(()=>({}));
 if(!r.ok)throw new Error(d.error||"API "+r.status);
 return d;
}
function setState(id,text,kind){const el=$(id);el.textContent=text;el.className="state "+kind;}
async function refreshAutoTrading(){
 const toggle=$("autoTradingEnabled"),state=$("autoTradingState"),help=$("autoTradingHelp");
 if(!toggle||!state||!help)return;
 try{
  const d=await api("/api/auto-trading");
  toggle.checked=!!d.trading_enabled;
  toggle.disabled=(!mt5Connected&&!d.trading_enabled)||autoTradingBusy;
  state.textContent=d.trading_enabled?"مفعّل":"متوقف";
  if($("azAutoMirror"))$("azAutoMirror").textContent=d.trading_enabled?"مفعّل":"متوقف";
  state.className="state "+(d.trading_enabled?"active":"muted");
  help.textContent=mt5Connected?"التحكم مربوط بالخادم وMT5": "اتصل بـ MT5 أولاً لتفعيل التداول الآلي";
 }catch(e){
  toggle.disabled=true;state.textContent="غير مربوط";state.className="state muted";help.textContent=e.message;
 }
}
async function changeAutoTrading(){
 const toggle=$("autoTradingEnabled");if(!toggle||autoTradingBusy)return;
 const requested=toggle.checked;autoTradingBusy=true;toggle.disabled=true;
 try{
  const d=await api("/api/auto-trading",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({enabled:requested})});
  toggle.checked=!!d.trading_enabled;
  $("autoTradingState").textContent=d.trading_enabled?"مفعّل":"متوقف";
  $("autoTradingState").className="state "+(d.trading_enabled?"active":"muted");
  $("autoTradingHelp").textContent=d.message||"تم حفظ حالة التداول الآلي";
 }catch(e){
  toggle.checked=!requested;
  $("autoTradingState").textContent="لم يتم التغيير";
  $("autoTradingState").className="state muted";
  $("autoTradingHelp").textContent=e.message;
 }finally{
  autoTradingBusy=false;toggle.disabled=(!mt5Connected&&!toggle.checked);
 }
}
function setFloatingColor(el,value){el.classList.remove("pnl-positive","pnl-negative","pnl-neutral");const n=Number(value);el.classList.add(!Number.isFinite(n)||n===0?"pnl-neutral":n>0?"pnl-positive":"pnl-negative");}
function escapeHtml(v){return String(v??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));}
async function boot(){
 try{
  const cfg=await fetch("/api/config").then(r=>r.json());
  if(!cfg.ready)throw Error("إعدادات تسجيل الدخول غير مكتملة");
  supabaseClient=window.supabase.createClient(cfg.supabase_url,cfg.supabase_anon_key);
  const {data}=await supabaseClient.auth.getSession();
  if(!data.session){location.href="/";return;}
  accessToken=data.session.access_token;
  if($("azCurrentUser"))$("azCurrentUser").textContent=data.session.user?.user_metadata?.name||data.session.user?.email||"حساب التداول";
  const access=await api("/api/access/check");
  if(access?.role==="owner"&&$("aiMonitorInline"))$("aiMonitorInline").hidden=false;
  $("serviceState").textContent="تم التحقق من الحساب";
  await refresh();
  if(access?.role==="owner")await refreshInlineMonitor();
 }catch(e){
  $("serviceState").textContent="يتطلب تسجيل الدخول";
  $("dataNotice").innerHTML="<b>تعذر التحقق من الحساب</b><span>"+escapeHtml(e.message)+" — ارجع إلى صفحة الدخول.</span>";
 }
}
async function refresh(){
 $("clock").textContent=new Intl.DateTimeFormat("en-AE-u-nu-latn",{dateStyle:"medium",timeStyle:"short"}).format(new Date());
 const fmt=v=>v==null?"—":new Intl.NumberFormat("en-US",{maximumFractionDigits:2}).format(v);
 try{
  const d=await api("/api/connection/status");
  mt5Connected=!!d.connected;
  if(d.connected){
   setState("mt5Status","متصل","pending");
   const a=d.connection?.account||{};
   if(d.connection?.symbol)setMarketChart(d.connection.symbol);
   $("mt5Meta").textContent="آخر نبضة: "+(d.connection?.last_seen||"وصلت من MT5")+(a.server?" · "+a.server:"");
   azUpdateConnectionDisplay(true,d.connection?.last_seen);
  }else{
   setState("mt5Status","غير متصل","muted");
   $("mt5Meta").textContent="لم تصل نبضة خلال آخر 30 ثانية";
   azUpdateConnectionDisplay(false);
  }
 }catch(e){
  mt5Connected=false;
  setState("mt5Status","غير متاح","muted");
  $("mt5Meta").textContent=e.message;
  azUpdateConnectionDisplay(false);
 }
 await refreshAutoTrading();
 shadowRefreshMarket();
 azRefreshTradingSettings();
 azRefreshAiCouncil();
 try{
  const s=await api("/api/dashboard/summary");
  shadowRenderPositions(s);
  const currency=s.currency||"";
  $("balance").textContent=s.balance==null?"—":fmt(s.balance)+" "+currency;
  $("floating").textContent=s.floating_pl==null?"—":fmt(s.floating_pl)+" "+currency;
  setFloatingColor($("floating"),s.floating_pl);
  $("openTrades").textContent=s.open_trades==null?"—":fmt(s.open_trades);
  $("winRate").textContent=s.win_rate==null?"—":fmt(s.win_rate)+"%";
  $("balanceMeta").textContent=s.equity==null?"لا توجد بيانات حقوق الحساب":"حقوق الحساب: "+fmt(s.equity)+" "+currency;
  const daily=s.daily||{};
  const dailyProfitEl=$("dailyProfit");if(dailyProfitEl)dailyProfitEl.textContent=daily.profit==null?"—":fmt(daily.profit)+" "+currency;
  const dailyLossEl=$("dailyLoss");if(dailyLossEl)dailyLossEl.textContent=daily.loss==null?"—":fmt(daily.loss)+" "+currency;
  const dailyNetEl=$("dailyNet");if(dailyNetEl)dailyNetEl.textContent=daily.net==null?"—":fmt(daily.net)+" "+currency;
  if(dailyNetEl){dailyNetEl.style.removeProperty("color");setFloatingColor(dailyNetEl,daily.net)}
  const dailyTradesEl=$("dailyTrades");if(dailyTradesEl)dailyTradesEl.textContent=daily.trades==null?"—":fmt(daily.trades);
  $("dailyMeta").textContent="صفقات محفوظة في السجل: "+fmt(s.history_records||0)+" · المراكز المغلقة: "+fmt(s.closed_count||0)+(s.history_error?" · خطأ السجل: "+s.history_error:"");
  const rows=Array.isArray(s.closed_trades)?s.closed_trades:[];
 if($("studioDayNet")){$("studioDayNet").textContent=daily.net==null?"—":fmt(daily.net)+" "+currency;setFloatingColor($("studioDayNet"),daily.net);}
 if($("studioEquity"))$("studioEquity").textContent=s.equity==null?"—":fmt(s.equity)+" "+currency;
 if($("dailyNet")){$("dailyNet").style.removeProperty("color");setFloatingColor($("dailyNet"),daily.net);}
 
  azDrawClientMiniChart(rows,s.currency||"");
  if($("azPerformanceNote"))$("azPerformanceNote").textContent="نتائج آخر "+rows.length+" مركز مغلق مستلم من سجل MT5؛ ليست توقعات";
  $("closedTrades").innerHTML=rows.length?rows.map(t=>{
   const date=t.time?new Intl.DateTimeFormat("en-GB-u-nu-latn",{dateStyle:"short",timeStyle:"short",timeZone:"Asia/Dubai"}).format(new Date(t.time*1000)):"—";
   const side=Number(t.type)===0?"شراء":Number(t.type)===1?"بيع":"—";
   const net=Number(t.net||0);
   return "<tr><td>"+escapeHtml(date)+"</td><td>"+escapeHtml(t.symbol||"—")+"</td><td>"+side+"</td><td>"+fmt(t.volume)+"</td><td class='"+(net>=0?"positive":"negative")+"'>"+fmt(net)+" "+escapeHtml(currency)+"</td></tr>";
  }).join(""):"<tr><td colspan='5' class='empty-cell'>لا توجد صفقات مغلقة في سجل MT5 المستلم بعد</td></tr>";
  $("floatingMeta").textContent=s.connected?"الربح/الخسارة العائمة من حساب MT5":"بانتظار اتصال MT5";
  if(s.connected){
   $("dataNotice").innerHTML="<b>بيانات MT5 مستلمة</b><span>تم استلام بيانات الحساب والصفقات. الشموع والسعر تُرسل من الشارت المفتوح في MT5.</span>";
   $("serviceState").textContent="بيانات MT5 مباشرة";
  }
 }catch(e){
  $("balanceMeta").textContent="تعذر تحميل بيانات الحساب";
  $("floatingMeta").textContent=e.message;
  $("dailyMeta").textContent="تعذر جلب سجل الصفقات: "+e.message;
  $("closedTrades").innerHTML="<tr><td colspan='5' class='empty-cell'>تعذر تحميل سجل MT5: "+escapeHtml(e.message)+"</td></tr>";
 }
}

function azUpdateConnectionDisplay(connected,lastSeen=null){
 const live=$("studioLive");const dot=$("studioDot");
 if(live)live.textContent=connected?"MT5 متصل":"MT5 غير متصل";
 if(dot)dot.classList.toggle("online",connected);
 const title=$("azBannerTitle"),sub=$("azBannerSub"),icon=$("azBannerCheck"),badge=$("azBannerMt5"),badgeText=$("azBannerMt5Text");
 if(title)title.textContent=connected?"حسابك نشط وMT5 متصل":"حسابك معتمد — MT5 غير متصل";
 if(sub)sub.textContent=connected?"بيانات الطرفية متاحة؛ التداول يعتمد على حالة التفعيل":"تحتاج اتصال MT5 فعلي لاستقبال الأسعار والمراكز";
 if(icon)icon.classList.toggle("disconnected",!connected);
 if(badge)badge.classList.toggle("connected",connected);
 if(badgeText)badgeText.textContent=connected?"متصل":"غير متصل";
 if($("azQuickConnection"))$("azQuickConnection").textContent=connected?"MT5 متصل":"MT5 غير متصل";
 if($("azLastSeen"))$("azLastSeen").textContent=lastSeen?String(lastSeen).replace("T"," ").slice(0,19):"—";
}
async function azRefreshTradingSettings(){
 try{
  const d=await api("/api/trade-management/settings");
  const p=d.settings?.position_management||{};
  // Show only values with a documented current source, never use recovery/add-on lots as base entry lot.
  if($("azLotValue"))$("azLotValue").textContent=Number.isFinite(Number(p.base_lot))&&Number(p.base_lot)>0?Number(p.base_lot).toFixed(2):"—";
  if($("azMaxPositions"))$("azMaxPositions").textContent=Number.isInteger(Number(p.max_positions))&&Number(p.max_positions)>0?String(p.max_positions):"—";
  if($("azManagementMode"))$("azManagementMode").textContent=String(p.mode||"—");
  // BUY/SELL controls remain disabled until the central engine supports directional toggles.
  if($("azBuyToggle")){$("azBuyToggle").checked=p.buy_enabled===true;$("azBuyToggle").disabled=true}
  if($("azSellToggle")){$("azSellToggle").checked=p.sell_enabled===true;$("azSellToggle").disabled=true}
 }catch(e){
  if($("azManagementMode"))$("azManagementMode").textContent="غير متاح";
 }
}
async function azRefreshAiCouncil(){
 const put=(id,value)=>{const el=$(id);if(el)el.textContent=value};
 const badge=(id,ready,text)=>{const el=$(id);if(!el)return;
   el.textContent=text;el.classList.toggle("good",ready===true);el.classList.toggle("bad",ready===false)};
 try{
  const d=await api("/api/ai/council");
  if(!d.connected){
    put("azAiCouncilStatus","MT5 غير متصل");
    badge("azCouncilState",null,"WAIT");
    badge("azRiskState",null,"WAIT");
    put("azFastVote","لا توجد بيانات MT5");put("azQwenVote","غير متاح");
    put("azDeepVote","غير متاح");put("azGroqVote","غير متاح");put("azRiskVote","غير متاح");
    put("azEngineSignal","WAIT");put("azCouncilBarTime","—");
    put("azCouncilReason","الطرفية غير متصلة؛ لا تُعرض موافقة دخول.");
    put("azRiskReason","لا توجد بيانات حساب حديثة للتحقق من المخاطر.");
    return;
  }
  const c=d.council||{},risk=d.risk_guardian||{},votes=c.votes||{};
  const signal=String(d.signal||"WAIT").toUpperCase();
  const fmtVote=(key)=>{
    const v=votes[key];
    if(!v)return "لم يصل تصويت";
    const state=String(v.status||"PENDING");
    const decision=String(v.direction||"WAIT");
    const valid=Number(v.confidence);
    const confidence=Number.isFinite(valid)?(" · "+Math.round(valid*100)+"%"):"";
    return state+" · "+decision+confidence;
  };
  put("azFastVote",fmtVote("Fast AI"));put("azQwenVote",fmtVote("Qwen"));
  put("azDeepVote",fmtVote("DeepSeek"));
  const groq=c.groq_advisory||{};
  const grStatus=String(groq.status||"NOT_CONFIGURED");
  put("azGroqVote",grStatus+(groq.status==="READY"?" · "+String(groq.direction||"WAIT")+
    " · "+Math.round(Number(groq.confidence||0)*100)+"%":"")+(groq.reason_ar?" · "+groq.reason_ar:""));
  const hybrid=d.mode==="HYBRID"||d.mode==="MASTER";
  const councilReady=hybrid?(signal==="BUY"||signal==="SELL"):c.approved===true;
  const allowed=councilReady&&risk.approved===true;
  badge("azCouncilState",councilReady,hybrid?d.mode:(c.approved===true?"APPROVED":"WAIT"));
  badge("azRiskState",risk.approved===true,risk.approved===true?"PASS":"BLOCK");
  put("azAiCouncilStatus",hybrid?(allowed?d.mode+" · "+signal:d.mode+" · WAIT"):(allowed?"APPROVED":"WAIT"));
  put("azEngineSignal",signal);
  put("azRiskVote",risk.approved===true?"PASS":"BLOCK");
  put("azCouncilReason",String(hybrid?(d.hybrid?.reason_ar||"نتائج AI تؤثر في الترجيح دون إجماع إلزامي"):(c.reason_ar||"المجلس ينتظر بيانات تصويت حديثة")));
  put("azRiskReason",String(risk.reason_ar||"لا يوجد تقرير مخاطر مكتمل"));
  const bar=Number(d.bar_time);
  put("azCouncilBarTime",Number.isFinite(bar)&&bar>0?
    new Intl.DateTimeFormat("en-GB-u-nu-latn",{timeZone:"Asia/Dubai",
      day:"2-digit",month:"2-digit",hour:"2-digit",minute:"2-digit"}).format(new Date(bar*1000)):"—");
 }catch(e){
  put("azAiCouncilStatus","غير متاح");badge("azCouncilState",null,"WAIT");
  badge("azRiskState",null,"WAIT");
  put("azCouncilReason","تعذر تحميل التصويت: "+(e.message||"خطأ اتصال"));
  put("azRiskReason","تعذر تحميل تقرير المخاطر.");
 }
}
$("azRefreshCouncilBtn")?.addEventListener("click",azRefreshAiCouncil);
function azDrawClientMiniChart(rows,currency){
 const host=$("azMiniPerformance");if(!host)return;
 const usable=(rows||[]).filter(r=>Number.isFinite(Number(r.net))&&Number.isFinite(Number(r.time))).slice(0,15).reverse();
 if(usable.length<2){host.innerHTML='<div class="az-mini-empty">لا توجد صفقات مغلقة كافية لرسم الأداء</div>';return}
 const values=[];let sum=0;
 for(const r of usable){sum+=Number(r.net);values.push(sum)}
 const lo=Math.min(0,...values),hi=Math.max(0,...values),span=Math.max(1,hi-lo),n=values.length;
 const x=i=>20+i/(n-1)*360;
 const y=v=>112-(v-lo)/span*94;
 const points=values.map((v,i)=>[x(i),y(v)]);
 const line=points.map((p,i)=>(i?"L":"M")+p[0].toFixed(1)+" "+p[1].toFixed(1)).join(" ");
 const dots=points.map(p=>'<circle cx="'+p[0].toFixed(1)+'" cy="'+p[1].toFixed(1)+'" r="3.8" fill="#8939ea"/>').join("");
 host.innerHTML='<svg role="img" aria-label="صافي نتائج آخر الصفقات المغلقة" viewBox="0 0 400 135" preserveAspectRatio="none"><defs><linearGradient id="azClientFill" x1="0" y1="0" x2="0" y2="1"><stop stop-color="#9951fa" stop-opacity=".46"/><stop offset="1" stop-color="#b98afa" stop-opacity=".02"/></linearGradient></defs>'+
 '<path d="'+line+' L 380 115 L 20 115 Z" fill="url(#azClientFill)"/>'+
 '<path d="'+line+'" fill="none" stroke="#8c35ed" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"/>'+dots+'</svg>';
}

const OLLAMA_URL="http://127.0.0.1:11434";
const OLLAMA_MODEL="qwen3.5:4b";
function aiState(message,kind="muted"){const el=$("aiStatus");if(el){el.textContent=message;el.className="state "+kind;}}
async function checkLocalOllama(){
 try{
  const response=await fetch(OLLAMA_URL+"/api/tags",{signal:AbortSignal.timeout(4500)});
  if(!response.ok)throw new Error("Ollama HTTP "+response.status);
  const data=await response.json();
  const models=Array.isArray(data.models)?data.models:[];
  const found=models.some(m=>String(m.name||"").split(":")[0]===OLLAMA_MODEL.split(":")[0] && String(m.name||"").includes(":3b"));
  if(found){aiState("جاهز","pending");$("aiHelp").textContent="Ollama يعمل والنموذج "+OLLAMA_MODEL+" موجود على هذا الجهاز.";}
  else{aiState("النموذج غير مثبت","muted");$("aiHelp").textContent="Ollama يعمل، لكن النموذج "+OLLAMA_MODEL+" غير موجود. ثبّت النموذج أولاً.";}
  return found;
 }catch(e){
  aiState("غير متصل","muted");
  $("aiHelp").textContent="تعذر الوصول إلى Ollama المحلي. شغّل Ollama واسمح لهذا الموقع بالاتصال به عبر OLLAMA_ORIGINS.";
  return false;
 }
}
function showAiError(message){$("aiResult").innerHTML="<div class='ai-error'>"+escapeHtml(message)+"</div>";}
async function runAiAnalysis(){
 const btn=$("aiAnalyzeBtn");btn.disabled=true;btn.textContent="جارٍ التحليل…";
 $("aiResult").innerHTML="<div class='ai-placeholder'>يتم فحص Ollama وجلب آخر شموع السوق من MT5…</div>";
 try{
  const ready=await checkLocalOllama();
  if(!ready)throw new Error("Ollama غير جاهز أو النموذج qwen3.5:4b غير مثبت. شغّل Ollama وتأكد من تثبيت النموذج.");
  const context=await api("/api/market/context");
  if(!Array.isArray(context.candles)||context.candles.length<20)throw new Error("بيانات الشموع غير كافية. افتح شارت BTCUSD أو XAUUSD في MT5 وانتظر وصول 20 شمعة مغلقة على الأقل.");
  const compact={
   symbol:context.symbol,timeframe:context.timeframe,tick:context.tick,
   candles:context.candles.slice(-120).map(c=>({time:c.time,open:c.open,high:c.high,low:c.low,close:c.close,volume:c.tick_volume}))
  };
  const systemPrompt="أنت محلل فني للسوق "+compact.symbol+". حلل البيانات المرفقة فقط ولا تخترع أسعاراً أو أخباراً أو مؤشرات غير محسوبة. أخرج JSON صالحاً فقط وبالعربية بهذه المفاتيح: signal ويكون BUY أو SELL أو WAIT، confidence رقم من 0 إلى 100، trend وصف قصير، momentum وصف قصير، support قيمة أو غير واضح، resistance قيمة أو غير واضح، reasons مصفوفة من 2 إلى 4 أسباب محددة مستندة إلى الشموع، risk_note تنبيه مختصر. لا تدّعِ ضمان الربح. إذا كانت البيانات متضاربة أو غير كافية اختر WAIT وثقة منخفضة. هذا تحليل استشاري فقط وليس أمراً بتنفيذ صفقة.";
  const response=await fetch(OLLAMA_URL+"/api/chat",{
   method:"POST",headers:{"Content-Type":"application/json"},
   body:JSON.stringify({model:OLLAMA_MODEL,stream:false,format:"json",options:{temperature:0.15},messages:[{role:"system",content:systemPrompt},{role:"user",content:JSON.stringify(compact)}]}),
   signal:AbortSignal.timeout(90000)
  });
  if(!response.ok)throw new Error("فشل طلب التحليل من Ollama: HTTP "+response.status);
  const result=await response.json();
  const raw=result.message?.content||"";
  let parsed;try{parsed=JSON.parse(raw);}catch(e){throw new Error("النموذج لم يُرجع JSON صالحاً. أعد المحاولة.");}
  const signal=String(parsed.signal||"WAIT").toUpperCase();
  const safeSignal=["BUY","SELL","WAIT"].includes(signal)?signal:"WAIT";
  const label=safeSignal==="BUY"?"شراء":safeSignal==="SELL"?"بيع":"انتظار";
  const reasons=Array.isArray(parsed.reasons)?parsed.reasons.slice(0,4):[];
  const facts=[["الاتجاه",parsed.trend],["الزخم",parsed.momentum],["الدعم",parsed.support],["المقاومة",parsed.resistance]].map(([k,v])=>"<div><small>"+k+"</small><b>"+escapeHtml(v??"غير واضح")+"</b></div>").join("");
  $("aiResult").innerHTML="<div class='ai-result-head'><span class='ai-signal "+safeSignal.toLowerCase()+"'>"+label+"</span><span class='ai-confidence'>درجة ثقة النموذج: "+escapeHtml(parsed.confidence??"—")+"%</span></div><div class='ai-facts'>"+facts+"</div><b style='font-size:11px'>أسباب القرار</b><ul class='ai-reasons'>"+(reasons.length?reasons.map(x=>"<li>"+escapeHtml(x)+"</li>").join(""):"<li>لم يقدم النموذج أسباباً مفصلة.</li>")+"</ul><div class='ai-risk'>"+escapeHtml(parsed.risk_note||"التحليل غير مضمون ولا ينفذ أوامر تداول.")+"</div>";
  aiState("تحليل جاهز","pending");
 }catch(e){showAiError(e.message||"تعذر إكمال التحليل.");}
 finally{btn.disabled=false;btn.textContent="تحليل السوق الآن";}
}
const aiAnalyzeButton=$("aiAnalyzeBtn");if(aiAnalyzeButton)aiAnalyzeButton.addEventListener("click",runAiAnalysis);
const autoTradingToggle=$("autoTradingEnabled");if(autoTradingToggle)autoTradingToggle.addEventListener("change",changeAutoTrading);

async function refreshInlineMonitor(){
 const card=$("aiMonitorInline");
 if(!card||card.hidden||!accessToken)return;
 try{
  const d=await api("/api/ai/monitor");
  shadowRenderDecision(d);
  const h=d.hybrid||{};
  const put=(id,v)=>{const node=$(id);if(node)node.textContent=String(v??"—");};
  put("azMonitorBase",h.base_direction||d.signal||"WAIT");
  put("azMonitorEffective",h.effective_direction||d.signal||"WAIT");
  put("azMonitorUsed",h.model_votes_used??0);
  put("azMonitorAiReason",h.reason_ar||"لا توجد مساهمة AI جديدة لهذه الشمعة");
  put("azMonitorReady",d.entry_ready?"جاهز للإرسال إلى MT5":"غير جاهز");
  put("azMonitorReason",d.reason_ar||"بانتظار بيانات القرار");
  put("azMonitorMode",d.mode==="MASTER"?"MASTER AI · العقل المركزي":d.mode==="ADVISORY"?"Hybrid AI الفعّال":d.mode||"—");
 }catch(e){
  const node=$("azMonitorReason");if(node)node.textContent="تعذر تحميل التشخيص: "+(e.message||"خطأ اتصال");
 }
}
$("azInlineMonitorRefresh")?.addEventListener("click",refreshInlineMonitor);
$("marketChartSymbol")?.addEventListener("change",e=>setMarketChart(e.target.value));
$("refreshBtn").addEventListener("click",refresh);

/* Shadow System visual composition. Read-only widgets use authenticated existing APIs. */
function shadowLayout(){
 if(document.getElementById("shadow-studio-v2"))return;
 document.body.classList.add("shadow-dashboard");
 const main=document.querySelector(".main");
 if(!main)return;
 const logo=main.querySelector(".az-user-logo");
 logo.innerHTML='<strong>AL-ZOOL 🇸🇩</strong><small>SHADOW SYSTEM · SUPER AI</small>';
 const top=main.querySelector(".az-user-top");
 const strip=document.createElement("div");strip.className="shadow-market-strip";
 strip.innerHTML='<span>₿ <b id="shadowMarketSymbol">BTCUSD</b> · M1</span><strong id="shadowCurrentPrice" dir="ltr">—</strong><span id="shadowMarketStamp">بانتظار أسعار MT5</span><span>BITCOIN SYSTEM / LIVE</span>';
 top.after(strip);
 const kpi=main.querySelector(".az-client-top-kpis"),ext=main.querySelector(".az-extended-kpis");
 if(kpi&&ext){[...ext.children].forEach(n=>kpi.appendChild(n));ext.remove();}
 const chart=main.querySelector(".chart-card");
 if(!chart)return;
 const wrap=chart.querySelector(".chart-wrap");
 wrap.innerHTML='<div id="shadowCandleChart" style="width:100%;min-height:260px;display:grid;place-items:center;color:#abb4db">بانتظار الشموع الحقيقية من MT5</div>';
 chart.querySelector("label.market-tag")?.remove();
 chart.querySelector(".chart-foot").textContent="شموع M1 من حساب MT5 المتصل · لا توجد أسعار أو إشارات افتراضية";
 chart.querySelector(".card-title").insertAdjacentHTML("beforeend",'<strong id="shadowChartPrice" class="shadow-price-label">—</strong>');
 setMarketChart(window.ALZOOL_MARKET_VIEW?.()||"BTCUSD");
 const ai=document.createElement("article");ai.className="card shadow-ai-panel";
 ai.innerHTML='<div class="shadow-ai-top"><div><b>AL-ZOOL AI</b><div class="shadow-ai-sub">المحرك الذكي المباشر</div></div><span class="shadow-crown" aria-hidden="true">♛</span></div><div class="shadow-ai-metrics"><div id="shadowSignal" class="shadow-signal" data-signal="WAIT"><small>الإشارة الحالية</small><strong>WAIT</strong></div><div class="shadow-confidence"><small>ثقة المحرك</small><strong id="shadowConfidence">—</strong><div class="shadow-meter"><i id="shadowMeterFill"></i></div></div></div><div class="shadow-ai-row"><span>وضع السوق</span><b id="shadowRegime">—</b></div><div class="shadow-ai-row"><span>حالة التنفيذ</span><b id="shadowExecution">بانتظار التحقق</b></div><div class="shadow-ai-row"><span>المساهمة الفعلية للنماذج</span><b id="shadowVotesUsed">—</b></div><div class="shadow-reason" id="shadowReason">بانتظار قرار حديث من المحرك.</div><a class="shadow-details-link" href="/ai-monitor.html">تفاصيل القرار وأسباب الدخول أو الانتظار ↗</a>';
 const stage=document.createElement("section");stage.className="shadow-stage";stage.append(chart,ai);kpi.after(stage);
 const bottom=document.createElement("section");bottom.className="shadow-bottom";stage.after(bottom);
 const performance=main.querySelector("#accountPerformance article:first-child");
 if(performance)bottom.append(performance);
 const positions=document.createElement("article");positions.className="card";
 positions.innerHTML='<div class="card-title"><h2>الصفقات المفتوحة</h2><a class="shadow-details-link" href="/trade-management.html#positions">إدارة</a></div><div class="table-scroll"><table class="history-table"><thead><tr><th>الرمز</th><th>الاتجاه</th><th>اللوت</th><th>الربح</th></tr></thead><tbody id="shadowPositions"><tr><td colspan="4">بانتظار MT5</td></tr></tbody></table></div>';
 bottom.append(positions);
 const history=$("closedHistory");if(history)bottom.append(history);
 const more=document.createElement("section");more.className="shadow-more";bottom.after(more);
 const groups=[["المحرك والذكاء الاصطناعي",["aiCouncilLive","aiMonitorInline"]],["إعدادات التداول والاتصال",["tradingSettingsPanel","accountPerformance"]]];
 for(const [name,ids] of groups){
  const d=document.createElement("details");const summary=document.createElement("summary");summary.textContent=name;d.append(summary);
  for(const id of ids){const n=$(id);if(n)d.append(n);}more.append(d);
 }
 const status=main.querySelector(".grid-main .status-card");
 const notice=$("dataNotice");
 if(status||notice){const d=document.createElement("details");d.innerHTML="<summary>حالة البيانات والتفاصيل اليومية</summary>";if(notice)d.append(notice);if(status)d.append(status);more.append(d);}
}
function shadowText(id,value){const x=$(id);if(x)x.textContent=value??"—";}
function shadowRenderPositions(s){
 const host=$("shadowPositions");if(!host)return;
 if(!s.connected){host.innerHTML='<tr><td colspan="4">MT5 غير متصل</td></tr>';return;}
 const positions=Array.isArray(s.positions)?s.positions:[];
 host.innerHTML=positions.length?positions.slice(0,20).map(p=>{
 const pnl=Number(p.profit||0)+Number(p.swap||0),side=Number(p.type)===0?"BUY":Number(p.type)===1?"SELL":"—";
 return "<tr><td>"+escapeHtml(p.symbol||"—")+"</td><td>"+side+"</td><td>"+escapeHtml(p.volume??"—")+"</td><td class='"+(pnl>=0?"positive":"negative")+"'>"+pnl.toFixed(2)+" "+escapeHtml(s.currency||"")+"</td></tr>";
 }).join(""):'<tr><td colspan="4">لا توجد صفقات مفتوحة</td></tr>';
}
let studioSignals=[];
function studioRenderStrength(context){
 const raw=context?.trend_strength;
 const n=raw!=null&&Number.isFinite(Number(raw))&&Number(raw)>=0&&Number(raw)<=1?Math.round(Number(raw)*100):null;
 shadowText("studioStrength",n==null?"—":n+"%");
 if($("studioArc"))$("studioArc").setAttribute("stroke-dasharray",(n??0)+" 100");
 shadowText("studioTrend",context?.regime||"بانتظار التحليل");
}
function studioRecordSignal(d){
 const host=$("studioSignals");if(!host||!d?.connected||!["BUY","SELL","WAIT"].includes(d.signal)||!Number.isFinite(Number(d.bar_time))||Number(d.bar_time)<=0)return;
 const bar=Number(d.bar_time),n=Number(d.confidence),confidence=d.confidence!=null&&Number.isFinite(n)&&n>=0&&n<=1?Math.round(n*100)+"%":"—";
 studioSignals=studioSignals.filter(x=>x.bar!==bar);
 studioSignals.unshift({bar,signal:d.signal,confidence});
 studioSignals=studioSignals.slice(0,5);
 host.innerHTML=studioSignals.map(x=>'<tr><td>'+new Intl.DateTimeFormat("en-GB",{hour:"2-digit",minute:"2-digit",timeZone:"Asia/Dubai",hourCycle:"h23"}).format(new Date(x.bar*1000))+'</td><td class="'+(x.signal==="BUY"?"positive":x.signal==="SELL"?"negative":"pnl-neutral")+'">'+x.signal+'</td><td>'+x.confidence+'</td></tr>').join("");
}
let shadowOwnerDecisionAt=0;
function shadowRenderDecision(d,authoritative=true){
 if(!d||!d.connected)return shadowClearDecision();
 if(authoritative)shadowOwnerDecisionAt=Date.now();
 if(!authoritative&&Date.now()-shadowOwnerDecisionAt<20000)return;
 const sig=["BUY","SELL","WAIT"].includes(d.signal)?d.signal:"WAIT";
 studioRenderStrength(d.strategy_context||d.context); if(authoritative)studioRecordSignal(d);
 const node=$("shadowSignal");if(node){node.dataset.signal=sig;node.querySelector("strong").textContent=sig;}
 const numeric=d.confidence!=null?Number(d.confidence):NaN;
 const confidence=Number.isFinite(numeric)&&numeric>=0&&numeric<=1?Math.round(numeric*100):null;
 shadowText("shadowConfidence",confidence==null?"—":confidence+"%");
 if($("shadowMeterFill"))$("shadowMeterFill").style.width=(confidence??0)+"%";
 shadowText("shadowRegime",d.strategy_context?.regime||d.context?.regime||"—");
 shadowText("shadowExecution",authoritative?(d.mt5_central_trading_enabled===false?"تنفيذ MT5 متوقف":d.trading_enabled===false?"التداول متوقف":d.entry_ready?"إشارة جاهزة · التنفيذ عند MT5":"انتظار"): "تحليل استشاري");
 shadowText("shadowVotesUsed",authoritative?(d.hybrid?.model_votes_used??"—"):"—");
 shadowText("shadowReason",d.reason_ar||d.decision_reason_ar||"بانتظار تفسير المحرك");
}
function shadowClearDecision(){
 studioRenderStrength(null);
 shadowOwnerDecisionAt=0;
 const node=$("shadowSignal");if(node){node.dataset.signal="WAIT";node.querySelector("strong").textContent="WAIT";}
 for(const [id,value] of [["shadowConfidence","—"],["shadowRegime","—"],["shadowExecution","MT5 غير متصل"],["shadowVotesUsed","—"],["shadowReason","لم تصل بيانات حديثة من الطرفية."]])shadowText(id,value);
 if($("shadowMeterFill"))$("shadowMeterFill").style.width="0%";
}
let shadowMarketBusy=false;
async function shadowRefreshMarket(){
 if(!accessToken||shadowMarketBusy)return;shadowMarketBusy=true;
 const host=$("shadowCandleChart");if(!host){shadowMarketBusy=false;return;}
 try{
  const data=await api("/api/market/context");
  if(!String(data.symbol||"").toUpperCase().startsWith(window.ALZOOL_MARKET_VIEW?.()||"BTCUSD"))throw Error("الرمز غير مطابق للسوق المعروض");
  setMarketChart(data.symbol);
  const candles=(Array.isArray(data.candles)?data.candles:[]).filter(c=>["open","high","low","close","time"].every(k=>Number.isFinite(Number(c[k])))).sort((a,b)=>Number(a.time)-Number(b.time));
  const compact=candles.slice(-76);
  const forming=data.forming_candle;
  if(forming&&["open","high","low","close","time"].every(k=>Number.isFinite(Number(forming[k])))&&(!compact.length||Number(forming.time)>Number(compact[compact.length-1].time)))compact.push(forming);
  const price=Number(data.tick?.bid||compact[compact.length-1]?.close||0);
  const fmt=n=>new Intl.NumberFormat("en-US",{minimumFractionDigits:2,maximumFractionDigits:2}).format(n);
  shadowText("shadowCurrentPrice",price>0?fmt(price):"—");shadowText("shadowChartPrice",price>0?fmt(price):"—");
  shadowText("shadowMarketStamp",data.received_at?"آخر بيانات MT5: "+String(data.received_at).replace("T"," ").slice(0,19):"بيانات حديثة");
  if(compact.length<2){host.textContent="لا توجد شموع كافية للرسم";return;}
  const lo=Math.min(...compact.map(c=>Number(c.low))),hi=Math.max(...compact.map(c=>Number(c.high))),span=Math.max(hi-lo,.001),padding=span*.07,minP=lo-padding,maxP=hi+padding;
  const W=900,H=402,left=12,right=811,top=14,bottom=270,dx=(right-left)/compact.length;
  const y=n=>top+(maxP-n)/(maxP-minP)*(bottom-top);
  const x=i=>left+dx*(i+.5);
  const maxVol=Math.max(1,...compact.map(c=>Number(c.tick_volume)||0));
  let grid="",bars="",vol="",labels="";
  for(let i=0;i<=5;i++){const n=maxP-i*(maxP-minP)/5,py=y(n);grid+='<path d="M12 '+py+' H811" stroke="#25324b" stroke-width=".7"/><text x="823" y="'+(py+4)+'" fill="#b6c6e8" font-size="11">'+fmt(n)+'</text>';}
  for(let i=0;i<compact.length;i++){
    const c=compact[i],px=x(i),up=Number(c.close)>=Number(c.open),color=up?"#39d6ed":"#b457fc",a=y(Number(c.open)),b=y(Number(c.close)),vh=(Number(c.tick_volume)||0)/maxVol*38;
    bars+='<path d="M'+px+' '+y(Number(c.high))+' V'+y(Number(c.low))+'" stroke="'+color+'" stroke-width="1.1"/><rect x="'+(px-dx*.29)+'" y="'+Math.min(a,b)+'" width="'+Math.max(1,dx*.58)+'" height="'+Math.max(1.2,Math.abs(a-b))+'" rx=".5" fill="'+color+'"><title>'+escapeHtml("O "+fmt(c.open)+" H "+fmt(c.high)+" L "+fmt(c.low)+" C "+fmt(c.close))+'</title></rect>';
    vol+='<rect x="'+(px-dx*.29)+'" y="'+(319-vh)+'" width="'+Math.max(1,dx*.58)+'" height="'+vh+'" fill="'+color+'" opacity=".52"/>';
    if(i%Math.max(1,Math.floor(compact.length/5))===0){const t=new Intl.DateTimeFormat("en-GB",{timeZone:"Asia/Dubai",hour:"2-digit",minute:"2-digit",hourCycle:"h23"}).format(new Date(Number(c.time)*1000));labels+='<text x="'+px+'" y="392" text-anchor="middle" font-size="10" fill="#acbbdf">'+t+'</text>';grid+='<path d="M'+px+' 14 V319" stroke="#1a2742" stroke-width=".6"/>';}
  }
  function ema(period,color){let v=Number(compact[0].close),a=2/(period+1);let p=compact.map((c,i)=>{v=i?a*Number(c.close)+(1-a)*v:v;return (i?"L":"M")+x(i).toFixed(2)+" "+y(v).toFixed(2)}).join(" ");return '<path d="'+p+'" fill="none" stroke="'+color+'" stroke-width="1.5" opacity=".82"/>';}
  // RSI 14 (Wilder smoothing), computed only from actual closed MT5 candles.
  const rsiMap=new Map();let ag=0,al=0;
  for(let i=1;i<candles.length;i++){
   const delta=Number(candles[i].close)-Number(candles[i-1].close),gain=Math.max(delta,0),loss=Math.max(-delta,0);
   if(i<=14){ag+=gain/14;al+=loss/14;}else{ag=(13*ag+gain)/14;al=(13*al+loss)/14;}
   if(i>=14)rsiMap.set(Number(candles[i].time),al===0?(ag===0?50:100):100-100/(1+ag/al));
  }
  const ry=v=>375-v*.48;
  let rsiPath="",rsiLast=null;
  compact.forEach((c,i)=>{const v=rsiMap.get(Number(c.time));if(v!=null){rsiPath+=(rsiPath?" L":"M")+x(i).toFixed(2)+" "+ry(v).toFixed(2);rsiLast=v;}});
  const rsiLayer='<rect x="12" y="324" width="799" height="53" fill="#160e2f"/><path d="M12 '+ry(70)+' H811 M12 '+ry(30)+' H811" stroke="#ba75ea" stroke-width=".7" stroke-dasharray="4 4"/><text x="18" y="334" font-size="10" fill="#daacff">RSI (14) '+(rsiLast==null?"—":rsiLast.toFixed(2))+'</text><text x="825" y="'+(ry(70)+3)+'" fill="#aebddd" font-size="9">70</text><text x="825" y="'+(ry(30)+3)+'" fill="#aebddd" font-size="9">30</text>'+(rsiPath?'<path d="'+rsiPath+'" fill="none" stroke="#c76bff" stroke-width="1.3"/>':"");
  const py=price>0?y(Math.max(minP,Math.min(maxP,price))):0;
  host.innerHTML='<svg class="shadow-chart" role="img" aria-label="شموع البيتكوين الحقيقية من MT5" viewBox="0 0 '+W+' '+H+'"><defs><linearGradient id="shadowChartBg" x2="1" y2="1"><stop stop-color="#071125"/><stop offset="1" stop-color="#130b29"/></linearGradient></defs><rect width="900" height="402" fill="url(#shadowChartBg)"/><text x="405" y="156" text-anchor="middle" font-size="65" fill="#8297e4" opacity=".06" font-weight="900">BTC · SYSTEM</text>'+grid+vol+ema(9,"#5ee3ff")+ema(21,"#ba6dff")+bars+(price>0?'<path d="M12 '+py+' H811" stroke="#44dce9" stroke-dasharray="4 4"/><rect x="813" y="'+(py-10)+'" width="87" height="20" rx="3" fill="#185e85"/><text x="855" y="'+(py+4)+'" text-anchor="middle" font-size="11" fill="white">'+fmt(price)+'</text>':'')+rsiLayer+labels+'</svg><div class="shadow-chart-note">EMA 9 / EMA 21 · RSI 14 · حجم تيكات MT5 · توقيت الإمارات</div>';
  const q=data.strategy_decision||{};
  shadowRenderDecision({connected:true,signal:q.decision,confidence:q.confidence,context:q.context,reason_ar:q.reason_ar},false);
 }catch(e){host.textContent="بانتظار بيانات MT5 الحية: "+String(e.message||"لا يوجد اتصال");shadowText("shadowCurrentPrice","—");shadowText("shadowChartPrice","—");shadowText("shadowMarketStamp","لا توجد بيانات حديثة");shadowClearDecision();}
 finally{shadowMarketBusy=false;}
}
shadowLayout();

boot();
setInterval(()=>{if(accessToken)refresh();},10000);
setInterval(refreshInlineMonitor,15000);
