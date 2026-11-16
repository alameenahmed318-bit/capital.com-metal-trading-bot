let supabaseClient=null,accessToken=null,mt5Connected=false,autoTradingBusy=false;
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
  await api("/api/access/check");
  $("serviceState").textContent="تم التحقق من الحساب";
  await refresh();
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
 azRefreshTradingSettings();
 azRefreshAiCouncil();
 try{
  const s=await api("/api/dashboard/summary");
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
  if(dailyNetEl){dailyNetEl.style.color=Number(daily.net)<0?"#d35470":"#28174a"}
  const dailyTradesEl=$("dailyTrades");if(dailyTradesEl)dailyTradesEl.textContent=daily.trades==null?"—":fmt(daily.trades);
  $("dailyMeta").textContent="صفقات محفوظة في السجل: "+fmt(s.history_records||0)+" · المراكز المغلقة: "+fmt(s.closed_count||0)+(s.history_error?" · خطأ السجل: "+s.history_error:"");
  const rows=Array.isArray(s.closed_trades)?s.closed_trades:[];
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
  const hybrid=d.mode==="HYBRID";
  const councilReady=hybrid?(signal==="BUY"||signal==="SELL"):c.approved===true;
  const allowed=councilReady&&risk.approved===true;
  badge("azCouncilState",councilReady,hybrid?"HYBRID":(c.approved===true?"APPROVED":"WAIT"));
  badge("azRiskState",risk.approved===true,risk.approved===true?"PASS":"BLOCK");
  put("azAiCouncilStatus",hybrid?(allowed?"HYBRID · "+signal:"HYBRID · WAIT"):(allowed?"APPROVED":"WAIT"));
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
 $("aiResult").innerHTML="<div class='ai-placeholder'>يتم فحص Ollama وجلب آخر شموع الذهب من MT5…</div>";
 try{
  const ready=await checkLocalOllama();
  if(!ready)throw new Error("Ollama غير جاهز أو النموذج qwen3.5:4b غير مثبت. شغّل Ollama وتأكد من تثبيت النموذج.");
  const context=await api("/api/market/context");
  if(!Array.isArray(context.candles)||context.candles.length<20)throw new Error("بيانات الشموع غير كافية. افتح شارت الذهب في MT5 وانتظر وصول 20 شمعة مغلقة على الأقل.");
  const compact={
   symbol:context.symbol,timeframe:context.timeframe,tick:context.tick,
   candles:context.candles.slice(-120).map(c=>({time:c.time,open:c.open,high:c.high,low:c.low,close:c.close,volume:c.tick_volume}))
  };
  const systemPrompt="أنت محلل فني للذهب XAUUSD. حلل البيانات المرفقة فقط ولا تخترع أسعاراً أو أخباراً أو مؤشرات غير محسوبة. أخرج JSON صالحاً فقط وبالعربية بهذه المفاتيح: signal ويكون BUY أو SELL أو WAIT، confidence رقم من 0 إلى 100، trend وصف قصير، momentum وصف قصير، support قيمة أو غير واضح، resistance قيمة أو غير واضح، reasons مصفوفة من 2 إلى 4 أسباب محددة مستندة إلى الشموع، risk_note تنبيه مختصر. لا تدّعِ ضمان الربح. إذا كانت البيانات متضاربة أو غير كافية اختر WAIT وثقة منخفضة. هذا تحليل استشاري فقط وليس أمراً بتنفيذ صفقة.";
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
 finally{btn.disabled=false;btn.textContent="تحليل الذهب الآن";}
}
const aiAnalyzeButton=$("aiAnalyzeBtn");if(aiAnalyzeButton)aiAnalyzeButton.addEventListener("click",runAiAnalysis);
const autoTradingToggle=$("autoTradingEnabled");if(autoTradingToggle)autoTradingToggle.addEventListener("change",changeAutoTrading);

$("refreshBtn").addEventListener("click",refresh);
boot();
setInterval(()=>{if(accessToken)refresh();},10000);
