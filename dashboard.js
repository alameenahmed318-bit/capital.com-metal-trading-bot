let supabaseClient=null,accessToken=null;
const $=id=>document.getElementById(id);
async function api(path){
 const r=await fetch(path,{headers:accessToken?{Authorization:"Bearer "+accessToken}:{}});
 const d=await r.json().catch(()=>({}));
 if(!r.ok)throw new Error(d.error||"API "+r.status);
 return d;
}
function setState(id,text,kind){const el=$(id);el.textContent=text;el.className="state "+kind;}
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
  await api("/api/access/check");
  $("serviceState").textContent="تم التحقق من الحساب";
  await refresh();
 }catch(e){
  $("serviceState").textContent="يتطلب تسجيل الدخول";
  $("dataNotice").innerHTML="<b>تعذر التحقق من الحساب</b><span>"+escapeHtml(e.message)+" — ارجع إلى صفحة الدخول.</span>";
 }
}
async function refresh(){
 $("clock").textContent=new Intl.DateTimeFormat("ar-AE",{dateStyle:"medium",timeStyle:"short"}).format(new Date());
 const fmt=v=>v==null?"—":new Intl.NumberFormat("en-US",{maximumFractionDigits:2}).format(v);
 try{
  const d=await api("/api/connection/status");
  if(d.connected){
   setState("mt5Status","متصل","pending");
   const a=d.connection?.account||{};
   $("mt5Meta").textContent="آخر نبضة: "+(d.connection?.last_seen||"وصلت من MT5")+(a.server?" · "+a.server:"");
  }else{
   setState("mt5Status","غير متصل","muted");
   $("mt5Meta").textContent="لم تصل نبضة خلال آخر 30 ثانية";
  }
 }catch(e){
  setState("mt5Status","غير متاح","muted");
  $("mt5Meta").textContent=e.message;
 }
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
  const dailyTradesEl=$("dailyTrades");if(dailyTradesEl)dailyTradesEl.textContent=daily.trades==null?"—":fmt(daily.trades);
  $("dailyMeta").textContent="صفقات محفوظة في السجل: "+fmt(s.history_records||0)+" · المراكز المغلقة: "+fmt(s.closed_count||0)+(s.history_error?" · خطأ السجل: "+s.history_error:"");
  const rows=Array.isArray(s.closed_trades)?s.closed_trades:[];
  $("closedTrades").innerHTML=rows.length?rows.map(t=>{
   const date=t.time?new Intl.DateTimeFormat("en-GB",{dateStyle:"short",timeStyle:"short",timeZone:"Asia/Dubai"}).format(new Date(t.time*1000)):"—";
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

const OLLAMA_URL="http://127.0.0.1:11434";
const OLLAMA_MODEL="qwen2.5:3b";
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
  if(!ready)throw new Error("Ollama غير جاهز أو النموذج qwen2.5:3b غير مثبت. شغّل Ollama وتأكد من تثبيت النموذج.");
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

$("refreshBtn").addEventListener("click",refresh);
boot();
setInterval(()=>{if(accessToken)refresh();},10000);
