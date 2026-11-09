let sb=null,session=null,latestCredentials=null;
const $=id=>document.getElementById(id);
async function api(path,method="GET",body=null){const headers={"Content-Type":"application/json"};if(session&&session.access_token)headers.Authorization="Bearer "+session.access_token;const r=await fetch(path,{method,headers,body:body?JSON.stringify(body):undefined});const d=await r.json().catch(()=>({}));if(!r.ok)throw new Error(d.error||d.msg||"تعذر تنفيذ الطلب");return d}
function show(v){["loginView","adminView","clientView"].forEach(id=>$(id).classList.toggle("hidden",id!==v))}
function err(id,msg){$(id).textContent=msg||""}
function esc(v){return String(v??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]))}
async function start(){try{const c=await api("/api/config");$("connection").textContent=c.ready?"الخادم متصل":"إعداد الخادم غير مكتمل";if(!c.ready){err("loginError","يلزم ربط إعدادات Supabase بالخادم قبل تفعيل تسجيل الدخول.");return}if(!window.supabase){err("loginError","تعذر تحميل خدمة تسجيل الدخول.");return}sb=window.supabase.createClient(c.supabase_url,c.supabase_anon_key);sb.auth.onAuthStateChange((_event,newSession)=>{session=newSession});const s=await sb.auth.getSession();session=s.data.session;if(session)await afterLogin()}catch(e){$("connection").textContent="الخادم غير متاح";err("loginError",e.message)}}
$("loginForm").addEventListener("submit",async e=>{e.preventDefault();err("loginError","");try{const x=await sb.auth.signInWithPassword({email:$("loginEmail").value.trim(),password:$("loginPassword").value});if(x.error)throw x.error;session=x.data.session;await afterLogin()}catch(x){err("loginError",x.message||"بيانات الدخول غير صحيحة")}});
async function afterLogin(){try{const a=await api("/api/access/check","POST",{});if(a.role==="owner"){show("adminView");$("ownerLabel").textContent=a.email;await loadClients()}else{show("clientView");$("clientLabel").textContent=a.email}}catch(e){await sb.auth.signOut();session=null;show("loginView");err("loginError",e.message)}}
async function logout(){if(sb)await sb.auth.signOut();session=null;show("loginView")}
$("logoutBtn").addEventListener("click",logout);$("clientLogoutBtn").addEventListener("click",logout);$("refreshBtn").addEventListener("click",loadClients);
async function loadClients(){$("clientsList").innerHTML='<p class="muted">جاري تحميل الحسابات...</p>';try{const d=await api("/api/admin/clients"),rows=d.clients||[];$("clientCount").textContent=rows.length;$("activeCount").textContent=rows.filter(x=>x.active).length;$("clientsList").innerHTML=rows.length?rows.map(c=>'<div class="client-row"><div class="client-info"><b>'+esc(c.name||c.email)+'</b><small>'+esc(c.email)+'</small><span class="tag '+(c.active?"":"off")+'">'+(c.active?"نشط":"موقوف")+'</span></div><div class="client-actions"><button data-action="toggle" data-id="'+esc(c.id)+'" data-active="'+c.active+'">'+(c.active?"إيقاف":"تفعيل")+'</button><button class="danger" data-action="delete" data-id="'+esc(c.id)+'">حذف</button></div></div>').join(""):'<p class="muted">لا يوجد عملاء حتى الآن.</p>'}catch(e){$("clientsList").innerHTML='<p class="error">'+esc(e.message)+'</p>'}}
$("clientsList").addEventListener("click",async e=>{const b=e.target.closest("button[data-action]");if(!b)return;try{if(b.dataset.action==="delete"){if(!confirm("تأكيد حذف حساب العميل؟ لن يستطيع تسجيل الدخول بعد الحذف."))return;await api("/api/admin/clients/"+b.dataset.id,"DELETE")}else{await api("/api/admin/clients/"+b.dataset.id,"PATCH",{active:b.dataset.active!=="true"})}await loadClients()}catch(x){alert(x.message)}});
$("createForm").addEventListener("submit",async e=>{e.preventDefault();err("createError","");try{const d=await api("/api/admin/clients","POST",{name:$("clientName").value.trim(),email:$("clientEmail").value.trim()});latestCredentials=d;$("newEmail").textContent=d.email;$("newPassword").textContent=d.password;$("credentialBox").classList.remove("hidden");$("clientName").value="";$("clientEmail").value="";await loadClients()}catch(x){err("createError",x.message)}});
$("downloadCredentials").addEventListener("click",()=>{if(!latestCredentials)return;const engine=latestCredentials.engine||{};const t="AL-ZOOL GOLD SNIPER\nبيانات دخول العميل\nالبريد الإلكتروني: "+latestCredentials.email+"\nكلمة المرور: "+latestCredentials.password+"\n\nبيانات ربط محرك MT5\nرابط السيرفر: "+(latestCredentials.engine_server_url||"https://al-zool-gold-sniper.onrender.com")+"\nClient ID: "+(engine.client_id||"غير متوفر")+"\nClient Token: "+(engine.client_token||"غير متوفر")+"\n\nفي MT5 أضف رابط السيرفر إلى قائمة WebRequest المسموحة، ثم أدخل الرابط وClient ID وClient Token في إعدادات AL-ZOOL Central Client. لا تشارك هذا الملف مع أي شخص غير صاحب الحساب. احتفظ بالرمز السري بسرية.";const blob=new Blob(["\ufeff"+t],{type:"text/plain;charset=utf-8"}),url=URL.createObjectURL(blob),a=document.createElement("a");a.href=url;a.download="AL-ZOOL-client-access.txt";a.click();URL.revokeObjectURL(url)});
start();

/* AL-ZOOL web trading workspace — navigation shell; live execution remains locked until engine/MT5 validation. */
const dashboardRoot=document.createElement("section");
dashboardRoot.id="dashboardView";dashboardRoot.className="hidden";
document.querySelector(".wrap").insertBefore(dashboardRoot,document.querySelector("footer"));
const navPages=[
 ["home","الرئيسية","⌂"],["control","التحكم بالتداول","◉"],["gold","الذهب والاستراتيجيات","◆"],["candles","الشموع","▥"],["council","مجلس التصويت","◎"],["analysis","التحليل الذكي","⌁"],["lab","مختبر الفرص","⌕"],["families","عائلات الاستراتيجيات","▦"],["strategies","الاستراتيجيات والتركيبات","▤"],["trades","الصفقات والأداء","↗"],["open","الصفقات المفتوحة","◫"],["pending","الأوامر المعلقة والسجل","◷"],["daily","الأداء اليومي","▧"],["results","نتائج الذكاء الاصطناعي","✧"],["learning","التعلم والتكيف","⟳"],["logs","السجلات","≡"],["blackbox","AZ BLACK BOX","⬡"],["settings","الإعدادات","⚙"]];
const strategyFamilies=["EMA Trend","RSI Momentum","MACD Momentum","Bollinger Reversion","Donchian Breakout","ATR Expansion","Candle Momentum","EMA Pullback","Range Reversion","Hybrid Confluence","Stochastic Reversal","CCI Momentum","Williams %R Reversal","ROC Momentum","Adaptive Swing Trend","VWAP Deviation","Pivot Breakout","Inside Bar Breakout","Engulfing Reversal","Volume Pressure"];
let activeDashboardPage="home",dashboardRole="client",dashboardStatusTimer=null,tradingEnabled=false,mt5Connected=false;
function dashboardPageBody(id){
 const wait='<div class="engine-wait"><span class="wait-icon">⌁</span><b>بانتظار ربط محرك التداول</b><p>هذه الصفحة جاهزة ضمن لوحة التحكم. ستظهر البيانات الحقيقية بعد نشر المحرك المركزي وربط موصل MT5 والتحقق منه.</p><small>لا توجد بيانات سوق أو صفقات تجريبية معروضة على أنها حقيقية.</small></div>';
 if(id==="home")return '<div class="dash-cards"><article><small>اتصال المحرك</small><b id="engineHomeStatus" class="state-off">بانتظار الإعداد</b></article><article><small>اتصال MT5</small><b id="mt5HomeStatus" class="state-off">جارٍ التحقق...</b></article><article><small>الرمز</small><b>XAUUSD</b></article><article><small>حالة التداول</small><b id="tradeHomeStatus" class="state-off">متوقف</b></article></div><div class="dash-panel"><h3>ملخص المنصة</h3><p>تم تجهيز صفحات البوت داخل حسابك. لم يتم تفعيل أوامر شراء أو بيع من الويب بعد.</p>'+wait+'</div>';
 if(id==="families")return '<div class="dash-panel"><h3>عائلات الاستراتيجيات الأساسية</h3><p>يتم تحميل القائمة مباشرة من محرك الاستراتيجيات الأصلي.</p><div id="familyCatalog" class="family-grid"><p class="muted">جاري تحميل العائلات...</p></div><p class="muted">وجود العائلة في الكتالوج لا يعني أنها اجتازت اختبار ربحية.</p></div>';
 if(id==="strategies")return '<div class="dash-panel"><h3>كتالوج المحرك الأصلي</h3><div id="catalogStats" class="dash-cards"><article><small>الاستراتيجيات</small><b>—</b></article><article><small>التركيبات</small><b>—</b></article><article><small>العائلات</small><b>—</b></article></div><h4>الاستراتيجيات الـ200</h4><div id="strategyCatalog" class="catalog-list">جاري تحميل الاستراتيجيات...</div><details class="combo-details"><summary>عرض التركيبات الـ1000</summary><div id="combinationCatalog" class="catalog-list">جاري تحميل التركيبات...</div></details><p class="muted">هذا كتالوج المحرك الحقيقي. التقييم الحي يحتاج بيانات شموع من MT5.</p></div>';
 if(id==="control")return '<div class="dash-panel"><h3>التحكم بالتداول</h3><div class="trade-lock"><b id="tradeControlStatus">التداول متوقف افتراضياً</b><p>التشغيل يسمح لموصل MT5 بتنفيذ إشارات BUY/SELL الجديدة. إعدادات الإكسبرت الحالية: لوت 0.01، صفقة واحدة كحد أقصى، وقف 1.8 ATR، هدف 1.2 ATR، والتريلينغ مفعّل. لا تضغط تشغيل قبل التأكد من نوع الحساب ومخاطره.</p><div class="trade-actions"><button id="enableTradingBtn" class="dash-btn" disabled>بدء التداول</button><button id="disableTradingBtn" class="dash-btn" disabled>إيقاف التداول</button></div><small id="tradeControlNote">جارٍ التحقق من اتصال MT5...</small></div></div>';
 if(id==="settings")return '<div class="dash-panel"><h3>إعدادات التداول والحماية</h3><div class="settings-grid">'+["حجم اللوت","حد الصفقات المفتوحة","هدف ربح الصفقة","هدف الربح الإجمالي","حد الخسارة اليومي","وقف الخسارة","جني الربح","Trailing Stop","حارس الخسارة الذكي","منع الصفقات العكسية","فريم التحليل","وضع الاستراتيجية"].map(x=>'<label>'+x+'<input disabled placeholder="سيتم تفعيله بعد ربط المحرك"></label>').join("")+'</div>'+wait+'</div>';
 const titles={gold:"الذهب والاستراتيجيات",candles:"الشموع وحركة السوق",council:"مجلس التصويت",analysis:"التحليل الذكي",lab:"مختبر الفرص",trades:"الصفقات والأداء",open:"الصفقات المفتوحة",pending:"الأوامر المعلقة والسجل",daily:"الأداء اليومي",results:"نتائج الذكاء الاصطناعي",learning:"التعلم والتكيف",logs:"السجلات",blackbox:"AZ BLACK BOX"};
 return '<div class="dash-panel"><h3>'+titles[id]+'</h3>'+wait+'</div>';
}
function renderDashboard(){
 dashboardRoot.innerHTML='<div class="dash-top"><div><div class="dash-brand">AL-ZOOL <span>GOLD SNIPER</span></div><small>مركز التداول الذكي • XAUUSD</small></div><div class="dash-top-actions"><span class="engine-pill">المحرك: غير مربوط</span><button id="dashLogout" class="btn ghost">تسجيل الخروج</button></div></div><div class="dash-layout"><aside class="dash-side"><div class="dash-user">'+(dashboardRole==="owner"?"OWNER CONTROL":"CLIENT WORKSPACE")+'</div>'+navPages.map(p=>'<button class="dash-nav '+(p[0]===activeDashboardPage?"selected":"")+'" data-page="'+p[0]+'"><span>'+p[2]+'</span>'+p[1]+'</button>').join("")+(dashboardRole==="owner"?'<button class="dash-nav back-admin" data-page="admin">↩ إدارة العملاء</button>':"")+'</aside><main class="dash-main"><div class="dash-heading"><div><small>لوحة التحكم</small><h1>'+navPages.find(p=>p[0]===activeDashboardPage)[1]+'</h1></div><span id="engineMarketTag" class="market-tag">XAUUSD • بانتظار تهيئة المحرك</span></div>'+dashboardPageBody(activeDashboardPage)+'</main></div>';
 $("dashLogout").addEventListener("click",logout);
 if($("enableTradingBtn"))$("enableTradingBtn").addEventListener("click",()=>setTradingEnabled(true));
 if($("disableTradingBtn"))$("disableTradingBtn").addEventListener("click",()=>setTradingEnabled(false));
 dashboardRoot.querySelectorAll("[data-page]").forEach(b=>b.addEventListener("click",()=>{if(b.dataset.page==="admin"){show("adminView");return}activeDashboardPage=b.dataset.page;renderDashboard()}));
 if(activeDashboardPage==="families"||activeDashboardPage==="strategies")loadEngineCatalog();
 if(dashboardStatusTimer)clearInterval(dashboardStatusTimer);
 refreshEngineStatus();
 loadDashboardWorkspace();
 dashboardStatusTimer=setInterval(()=>{if(!$("dashboardView")?.classList.contains("hidden"))refreshEngineStatus()},5000);
}
async function refreshEngineStatus(){
 const pill=document.querySelector(".engine-pill"),home=$("engineHomeStatus"),mt5=$("mt5HomeStatus"),tag=$("engineMarketTag");
 try{
  const health=await fetch("/health",{cache:"no-store"});
  const h=await health.json().catch(()=>({}));
  const engineOnline=health.ok&&h.status==="online";
  if(home){home.textContent=engineOnline?"متصل":"غير متاح";home.className=engineOnline?"state-on":"state-off"}
  if(!engineOnline){
   if(pill)pill.textContent="المحرك المركزي: غير متاح";
   if(mt5){mt5.textContent="غير مؤكد";mt5.className="state-off"}
   if(tag)tag.textContent="XAUUSD • المحرك المركزي غير متاح";
   return;
  }
  const s=await api("/api/engine/status");
  const connected=s.mt5_connected===true;
  mt5Connected=connected;tradingEnabled=s.trading_enabled===true;
  const tradeHome=$("tradeHomeStatus"),tradeStatus=$("tradeControlStatus"),enableBtn=$("enableTradingBtn"),disableBtn=$("disableTradingBtn"),note=$("tradeControlNote");
  if(tradeHome){tradeHome.textContent=tradingEnabled?"مفعّل":"متوقف";tradeHome.className=tradingEnabled?"state-on":"state-off"}
  if(tradeStatus)tradeStatus.textContent=tradingEnabled?"التداول الآلي مفعّل":"التداول الآلي متوقف";
  if(enableBtn)enableBtn.disabled=!connected||tradingEnabled;
  if(disableBtn)disableBtn.disabled=!tradingEnabled;
  if(note)note.textContent=connected?"MT5 متصل — التشغيل يرسل إذن التنفيذ إلى الإكسبرت":"يلزم اتصال MT5 حديث قبل بدء التداول";
  if(pill)pill.textContent=connected?"المحرك وMT5: متصلان":"المحرك متصل • MT5 غير متصل";
  if(mt5){mt5.textContent=connected?"متصل":"بانتظار بيانات MT5";mt5.className=connected?"state-on":"state-off"}
  if(tag){
   const age=s.seconds_since_last_data;
   tag.textContent=connected?"XAUUSD • MT5 متصل • آخر بيانات منذ "+age+" ثانية":"XAUUSD • بانتظار بيانات MT5";
  }
 }catch(e){
  if(pill)pill.textContent="تعذر تحديث حالة الاتصال";
  if(mt5){mt5.textContent="تعذر التحقق";mt5.className="state-off"}
  if(tag)tag.textContent="XAUUSD • حالة الاتصال غير مؤكدة";
 }
}
async function setTradingEnabled(enabled){
 if(enabled&&!mt5Connected){alert("لا يمكن بدء التداول قبل تأكيد اتصال MT5.");return}
 if(enabled&&!confirm("تنبيه: قد ينفذ MT5 صفقات حقيقية فور ظهور إشارة جديدة. الإعداد الحالي 0.01 لوت، صفقة واحدة، وقف 1.8 ATR وهدف 1.2 ATR. هل تؤكد أنك راجعت نوع الحساب وإعدادات المخاطرة وتريد بدء التداول؟"))return;
 if(!enabled&&!confirm("تأكيد إيقاف فتح صفقات جديدة؟ ستظل حماية الصفقات المفتوحة (SL/TP/Trailing) تعمل محلياً."))return;
 try{await api("/api/engine/control","POST",{enabled});tradingEnabled=enabled;await refreshEngineStatus()}catch(e){alert("تعذر تغيير حالة التداول: "+e.message)}
}
async function loadEngineCatalog(){
 try{
  const d=await api("/api/engine/catalog");
  if($("familyCatalog"))$("familyCatalog").innerHTML=d.families.map((f,i)=>'<div class="family-item"><span>'+String(i+1).padStart(2,"0")+'</span>'+esc(f.name)+' <small>('+d.strategies.filter(s=>s.family===f.name).length+' إعداد)</small></div>').join("");
  if($("catalogStats"))$("catalogStats").innerHTML='<article><small>الاستراتيجيات</small><b>'+d.strategies.length+'</b></article><article><small>التركيبات</small><b>'+d.combinations.length.toLocaleString("en-US")+'</b></article><article><small>العائلات</small><b>'+d.families.length+'</b></article>';
  if($("strategyCatalog"))$("strategyCatalog").innerHTML=d.strategies.map(s=>'<div class="catalog-row"><b>#'+s.id+' '+esc(s.name)+'</b><small>'+esc(s.family)+' • EMA '+s.fast+'/'+s.slow+' • Period '+s.period+'</small></div>').join("");
  if($("combinationCatalog"))$("combinationCatalog").innerHTML=d.combinations.map(x=>'<div class="catalog-row"><b>'+esc(x.name)+'</b><small>Strategy IDs: '+x.members.join(" / ")+'</small></div>').join("");
 }catch(e){for(const id of ["familyCatalog","strategyCatalog","combinationCatalog"])if($(id))$(id).innerHTML='<p class="error">'+esc(e.message)+'</p>'}
}
function show(v){["loginView","adminView","clientView","dashboardView"].forEach(id=>{const el=$(id);if(el)el.classList.toggle("hidden",id!==v)})}
async function afterLogin(){
 try{
  const a=await api("/api/access/check","POST",{});
  if(a.role==="owner"){
   dashboardRole="owner";show("adminView");$("ownerLabel").textContent=a.email;await loadClients();
   let btn=$("openOwnerDashboard");if(!btn){btn=document.createElement("button");btn.id="openOwnerDashboard";btn.className="btn primary";btn.textContent="فتح لوحة التداول";$("adminView").querySelector(".welcome").appendChild(btn)}
   btn.onclick=()=>{activeDashboardPage="home";renderDashboard();show("dashboardView")};
   let ownerEngineBtn=$("ownerEngineCredentials");if(!ownerEngineBtn){ownerEngineBtn=document.createElement("button");ownerEngineBtn.id="ownerEngineCredentials";ownerEngineBtn.className="btn secondary";ownerEngineBtn.textContent="إصدار بيانات ربط حساب المالك لـ MT5";$("adminView").querySelector(".welcome").appendChild(ownerEngineBtn)}
   ownerEngineBtn.onclick=async()=>{if(!confirm("سيتم إصدار/تجديد بيانات ربط محرك حساب المالك. أي بيانات مالك سابقة ستتوقف. هل تتابع؟"))return;ownerEngineBtn.disabled=true;try{const d=await api("/api/owner/engine-credentials","POST",{});const e=d.engine||{};const t="AL-ZOOL GOLD SNIPER\nبيانات ربط محرك حساب المالك\nServer URL: "+d.engine_server_url+"\nOwner Engine ID: "+e.client_id+"\nOwner Engine Token: "+e.client_token+"\n\nاحتفظ بهذا الملف بسرية. أدخل القيم في InpEngineId و InpEngineToken داخل MT5.";const blob=new Blob(["\ufeff"+t],{type:"text/plain;charset=utf-8"}),url=URL.createObjectURL(blob),a=document.createElement("a");a.href=url;a.download="AL-ZOOL-owner-engine-access.txt";a.click();URL.revokeObjectURL(url);alert("تم إصدار بيانات ربط المالك وتنزيل الملف. أدخلها في MT5.");}catch(e){alert(e.message)}finally{ownerEngineBtn.disabled=false}};
  }else{dashboardRole="client";activeDashboardPage="home";renderDashboard();show("dashboardView")}
 }catch(e){await sb.auth.signOut();session=null;show("loginView");err("loginError",e.message)}
}

async function loadDashboardWorkspace(){
 const panel=document.querySelector(".dash-main .dash-panel");
 if(!panel)return;
 const id=activeDashboardPage;
 if(!["home","gold","analysis","council","lab","results","learning","daily","logs","blackbox"].includes(id))return;
 try{
  const d=await api("/api/engine/workspace"),a=d.analysis||{},x=a.context||{},z=a.adaptive||{},q=d.decision||{};
  const escv=v=>esc(v==null?"—":v),num=v=>Number.isFinite(Number(v))?Number(v).toFixed(2):"—";
  const tile=(k,v)=>'<article><small>'+escv(k)+'</small><b>'+escv(v)+'</b></article>';
  const grid=arr=>'<div class="dash-cards">'+arr.join("")+'</div>';
  let body="";
  if(id==="home"||id==="gold")body=grid([tile("حالة السوق",x.regime||"بانتظار البيانات"),tile("القرار",q.decision),tile("الاستراتيجية",q.strategy_name),tile("الثقة",q.confidence==null?"—":(Number(q.confidence)*100).toFixed(1)+"%"),tile("ATR",num(x.atr)),tile("قوة الاتجاه",num(x.trend_strength)),tile("جودة الشراء",num(x.buy_quality)),tile("جودة البيع",num(x.sell_quality))]);
  if(id==="analysis")body=grid([tile("نظام السوق",x.regime),tile("ATR Ratio",num(x.atr_ratio)),tile("ADX",num(x.adx)),tile("VWAP",num(x.vwap)),tile("الدعم",num(x.support)),tile("المقاومة",num(x.resistance)),tile("إعداد RSI",z.rsi_period),tile("التكيف",z.mode)])+'<p>'+escv(x.reason)+'</p>';
  if(id==="council"||id==="results")body=grid([tile("القرار المركزي",q.decision),tile("الاستراتيجية",q.strategy_name),tile("رقمها",q.strategy_id),tile("الثقة",q.confidence==null?"—":(Number(q.confidence)*100).toFixed(1)+"%")])+'<p>'+escv(q.reason_ar)+'</p>';
  if(id==="lab")body=grid([tile("جودة الشراء",num(x.buy_quality)),tile("جودة البيع",num(x.sell_quality)),tile("فخ شراء",x.buy_trap?"محتمل":"غير مرصود"),tile("فخ بيع",x.sell_trap?"محتمل":"غير مرصود")])+'<p>تحليل الشمعة الأخيرة؛ ليس اختباراً تاريخياً ولا ضماناً للربح.</p>';
  if(id==="learning")body=grid([tile("نتائج تعلم محفوظة",d.outcome_count),tile("أوزان الاستراتيجيات",Array.isArray(d.weights)?d.weights.length:0)])+'<div class="catalog-list">'+(d.outcomes||[]).map(v=>'<div class="catalog-row"><b>Strategy #'+escv(v.strategy_id)+'</b><small>Deal '+escv(v.deal_id)+' • P/L '+num(v.pnl)+'</small></div>').join("")+'</div>';
  if(id==="daily")body=grid([tile("نتائج مرسلة",d.outcome_count),tile("صافي النتائج المحفوظة",(d.outcomes||[]).reduce((s,v)=>s+Number(v.pnl||0),0).toFixed(2)),tile("بيانات السوق",d.connected?"حديثة":"غير حديثة")])+'<p>هذه النتائج المرسلة للتعلم فقط، وليست كشف حساب MT5 كاملاً.</p>';
  if(id==="logs"||id==="blackbox")body=grid([tile("Decision ID",q.decision_id),tile("القرار",q.decision),tile("الاستراتيجية",q.strategy_name),tile("آخر وصول",d.last_seen_at)])+'<pre>'+esc(JSON.stringify({reason:q.reason_ar,context:x,adaptive:z},null,2))+'</pre>';
  if(!d.connected)body='<p class="muted">لا توجد بيانات سوق حديثة من MT5. '+escv(d.reason)+'</p>'+body;
  panel.innerHTML='<h3>بيانات المحرك الفعلية</h3>'+body;
 }catch(e){panel.innerHTML='<h3>بيانات المحرك</h3><p class="error">'+esc(e.message)+'</p>'}
}
