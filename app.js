let sb=null,session=null,latestCredentials=null,supabaseReady=false;
const $=id=>document.getElementById(id);
async function api(path,method="GET",body=null){const headers={"Content-Type":"application/json"};if(session?.access_token)headers.Authorization="Bearer "+session.access_token;const r=await fetch(path,{method,headers,body:body?JSON.stringify(body):null});const d=await r.json().catch(()=>({}));if(!r.ok)throw new Error(d.error||"تعذر تنفيذ الطلب");return d}
function show(id){["loginView","adminView","clientView"].forEach(x=>$(x).classList.toggle("hidden",x!==id))}
function msg(id,value){$(id).textContent=value||""}
async function boot(){try{const response=await fetch("/api/config",{cache:"no-store"});if(!response.ok)throw Error("تعذر قراءة إعدادات الخادم ("+response.status+")");const c=await response.json();if(!c.ready){const missing=Array.isArray(c.missing)?c.missing:[];throw Error("إعدادات Supabase غير مكتملة على Render"+(missing.length?" — المتغيرات الناقصة: "+missing.join(", "):" — راجع متغيرات البيئة"));}if(!window.supabase||typeof window.supabase.createClient!=="function")throw Error("تعذر تحميل مكتبة Supabase؛ حدّث الصفحة أو تحقق من الاتصال.");if(!c.supabase_url||!c.supabase_anon_key)throw Error("رابط Supabase أو المفتاح العام غير موجود.");sb=window.supabase.createClient(c.supabase_url,c.supabase_anon_key);if(!sb?.auth)throw Error("تعذر تهيئة تسجيل الدخول عبر Supabase.");supabaseReady=true;const {data,error}=await sb.auth.getSession();if(error)throw error;session=data.session;if(session)await route();else show("loginView");}catch(e){supabaseReady=false;sb=null;$("connection").textContent="الخدمة تحتاج إعداد";msg("loginError",e.message||"تعذر تهيئة Supabase");show("loginView");}}
async function route(){const a=await api("/api/access/check");if(a.role==="owner"){show("adminView");$("ownerLabel").textContent=a.email;await refreshClients();await refreshConnection();await refreshCouncil();await refreshVersions();await refreshResearch();await refreshActionControl();await refreshAdminOverview()}else{location.href="/dashboard.html";return}}
$("loginForm").addEventListener("submit",async e=>{e.preventDefault();msg("loginError","");if(!supabaseReady||!sb?.auth){msg("loginError","تسجيل الدخول متوقف لأن إعدادات Supabase لم تكتمل بعد. راجع رسالة إعداد الخادم أعلاه.");return;}try{const {data,error}=await sb.auth.signInWithPassword({email:$("loginEmail").value.trim(),password:$("loginPassword").value});if(error)throw error;session=data.session;await route()}catch(x){msg("loginError",x.message||"تعذر تسجيل الدخول")}});
async function logout(){await sb.auth.signOut();session=null;show("loginView")}
$("logoutBtn").addEventListener("click",logout);$("clientLogoutBtn").addEventListener("click",logout);
async function refreshClients(){const d=await api("/api/admin/clients");azSetAdminClients(d.clients);$("clientCount").textContent=d.clients.length;$("activeCount").textContent=d.clients.filter(x=>x.active).length;const host=$("clientsList");host.replaceChildren();if(!d.clients.length){host.textContent="لا يوجد عملاء حالياً.";return}for(const c of d.clients){const card=document.createElement("article");card.className="client-row";const info=document.createElement("div");const name=document.createElement("b");name.textContent=c.name||c.email;const meta=document.createElement("small");meta.textContent=c.email+" • "+(c.licensed?"ترخيص موجود":"لا يوجد ترخيص");info.append(name,meta);const actions=document.createElement("div");actions.className="actions";const toggle=document.createElement("button");toggle.className="btn small "+(c.active?"ghost":"secondary");toggle.textContent=c.active?"إيقاف":"تفعيل";toggle.onclick=async()=>{try{await api("/api/admin/clients/"+c.id,"PATCH",{active:!c.active});await refreshClients()}catch(e){alert(e.message)}};const del=document.createElement("button");del.className="btn danger small";del.textContent="حذف";del.onclick=async()=>{if(!confirm("حذف حساب هذا العميل؟"))return;try{await api("/api/admin/clients/"+c.id,"DELETE");await refreshClients()}catch(e){alert(e.message)}};actions.append(toggle,del);card.append(info,actions);host.append(card)}}
// Dark admin dashboard — real account/client data only; never generates fake P/L.
let azAdminRecent=[],azAdminTrades=[],azAdminCurrency="",azAdminRange=7;
const azAdminNumber=v=>v==null||!Number.isFinite(Number(v))?"—":new Intl.NumberFormat("en-US",{maximumFractionDigits:2}).format(Number(v));
const azAdminMoney=v=>v==null?"—":azAdminNumber(v)+(azAdminCurrency?" "+azAdminCurrency:"");
function azSetAdminClients(rows){
  azAdminRecent=Array.isArray(rows)?rows:[];
  const total=$("adminClientCount"),active=$("adminActiveCount"),table=$("adminRecentClients");
  if(total)total.textContent=azAdminNumber(azAdminRecent.length);
  if(active)active.textContent=azAdminNumber(azAdminRecent.filter(c=>c.active).length);
  if(!table)return;
  table.replaceChildren();
  if(!azAdminRecent.length){
    const tr=document.createElement("tr");const td=document.createElement("td");
    td.colSpan=3;td.textContent="لا يوجد عملاء مسجلون";tr.append(td);table.append(tr);return;
  }
  for(const c of azAdminRecent.slice(0,5)){
    const tr=document.createElement("tr");
    for(const value of [c.name||c.email||"—",c.email||"—",c.active?"نشط":"موقوف"]){
      const td=document.createElement("td");td.textContent=value;tr.append(td);
    }
    table.append(tr);
  }
}
function azDrawAdminChart(){
 const host=$("adminPerformanceChart");if(!host)return;
 const now=new Date(),days=azAdminRange;
 const dates=[],values=[],totals=new Map();
 const dateKey=d=>new Intl.DateTimeFormat("en-CA",{timeZone:"Asia/Dubai",year:"numeric",month:"2-digit",day:"2-digit"}).format(d);
 for(let i=days-1;i>=0;i--){const d=new Date(now.getTime()-i*86400000);dates.push(dateKey(d))}
 for(const t of azAdminTrades){
  if(!Number.isFinite(Number(t.time)))continue;
  const key=dateKey(new Date(Number(t.time)*1000));
  if(dates.includes(key))totals.set(key,(totals.get(key)||0)+Number(t.net||0));
 }
 if(!azAdminTrades.length||!Array.from(totals.values()).some(x=>Number.isFinite(x))){
   host.innerHTML='<div class="az-chart-empty">لا توجد صفقات مغلقة مسجلة لهذه الفترة</div>';return;
 }
 let cumulative=0;
 for(const d of dates){cumulative+=totals.get(d)||0;values.push(cumulative)}
 const min=Math.min(0,...values),max=Math.max(0,...values),span=Math.max(1,max-min);
 const x=i=>52+(i/(Math.max(1,values.length-1)))*710;
 const y=v=>205-((v-min)/span)*160;
 const pts=values.map((v,i)=>[x(i),y(v)]);
 const line=pts.map((p,i)=>(i?"L":"M")+p[0].toFixed(1)+" "+p[1].toFixed(1)).join(" ");
 const area=line+" L "+x(values.length-1).toFixed(1)+" 208 L 52 208 Z";
 const grids=Array.from({length:5},(_,i)=>'<line x1="52" y1="'+(45+i*40)+'" x2="762" y2="'+(45+i*40)+'" stroke="#6b4497" opacity=".34" />').join("");
 const labels=dates.map((date,i)=>i===0||i===dates.length-1||days<=7?
 '<text x="'+x(i).toFixed(1)+'" y="237" text-anchor="middle" font-size="11" fill="#c6b3e4">'+date.slice(5)+'</text>':"").join("");
 const circles=pts.map(p=>'<circle cx="'+p[0].toFixed(1)+'" cy="'+p[1].toFixed(1)+'" r="3.7" fill="#fff" stroke="#b459ff" stroke-width="2" />').join("");
 host.innerHTML='<svg viewBox="0 0 800 252" role="img" aria-label="صافي نتائج الصفقات المغلقة عبر الأيام" preserveAspectRatio="none"><defs><linearGradient id="azProfitArea" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stop-color="#9c32ff" stop-opacity=".55"/><stop offset="100%" stop-color="#9c32ff" stop-opacity=".04"/></linearGradient></defs>'+
 grids+'<path d="'+area+'" fill="url(#azProfitArea)"/><path d="'+line+'" fill="none" stroke="#b25bff" stroke-width="3.2" stroke-linejoin="round" stroke-linecap="round"/>'+circles+
 '<text x="52" y="26" font-size="12" fill="#f5e8ff">'+azAdminMoney(cumulative)+'</text>'+labels+'</svg>';
}
async function refreshAdminOverview(){
 if($("adminView")?.classList.contains("hidden"))return;
 const clock=$("adminClock");
 if(clock)clock.textContent=new Intl.DateTimeFormat("en-GB-u-nu-latn",{dateStyle:"medium",timeStyle:"short",timeZone:"Asia/Dubai"}).format(new Date());
 try{
  const status=await api("/api/connection/status");
  if($("adminConnectionLine"))$("adminConnectionLine").textContent=status.connected?"MT5 متصل بالمحرك المركزي":"MT5 غير متصل حالياً";
  if($("adminLiveDot"))$("adminLiveDot").classList.toggle("off",!status.connected);
 }catch(e){
  if($("adminConnectionLine"))$("adminConnectionLine").textContent="تعذر فحص اتصال MT5";
  $("adminLiveDot")?.classList.add("off");
 }
 try{
  const d=await api("/api/dashboard/summary");
  azAdminTrades=Array.isArray(d.closed_trades)?d.closed_trades:[];
  azAdminCurrency=d.currency||"";
  if($("adminOpenTrades"))$("adminOpenTrades").textContent=d.connected?azAdminNumber(d.open_trades):"—";
  if($("adminDailyNet")){
    const v=d.daily?.net;
    const el=$("adminDailyNet");el.textContent=v==null?"—":azAdminMoney(v);
    el.classList.toggle("positive",v>0);el.classList.toggle("negative",v<0);
  }
  azDrawAdminChart();
 }catch(e){
  if($("adminOpenTrades"))$("adminOpenTrades").textContent="—";
  if($("adminDailyNet"))$("adminDailyNet").textContent="—";
  if($("adminPerformanceChart"))$("adminPerformanceChart").textContent="تعذر قراءة سجل MT5: "+e.message;
 }
}
for(const button of document.querySelectorAll("[data-admin-days]")){
 button.addEventListener("click",()=>{
  azAdminRange=Number(button.dataset.adminDays)||7;
  document.querySelectorAll("[data-admin-days]").forEach(x=>x.classList.toggle("active",x===button));
  azDrawAdminChart();
 });
}
$("adminSidebarLogoutBtn")?.addEventListener("click",logout);

let ownerPairingData=null;
$("ownerPairBtn").addEventListener("click",async()=>{
  msg("ownerPairError","");
  if(!confirm("ربط MT5 للمالك: إصدار مفتاح جديد سيُلغي أي اقتران سابق. متابعة؟"))return;
  const btn=$("ownerPairBtn");btn.disabled=true;
  try{
    const d=await api("/api/owner/terminal-pair","POST",{});
    ownerPairingData={InpOwnerMode:true,InpServerUrl:d.engine_server_url,InpOwnerConnectionId:d.engine_id,InpOwnerPairingKey:d.pairing_key,InpEnableCentralTrading:false};
    $("ownerPairServer").textContent=d.engine_server_url;
    $("ownerPairId").textContent=d.engine_id;
    $("ownerPairKey").textContent=d.pairing_key;
    $("ownerPairResult").classList.remove("hidden");
  }catch(e){msg("ownerPairError",e.message||"فشل اقتران جهاز المالك")}
  finally{btn.disabled=false}
});
$("ownerPairDownload").addEventListener("click",()=>{
  if(!ownerPairingData)return;
  const blob=new Blob([JSON.stringify(ownerPairingData,null,2)],{type:"application/json"});
  const a=document.createElement("a");a.href=URL.createObjectURL(blob);
  a.download="AL-ZOOL-Owner-MT5-Pairing.json";a.click();URL.revokeObjectURL(a.href);
});
$("refreshBtn").addEventListener("click",async()=>{await refreshClients();await refreshConnection()});
$("createForm").addEventListener("submit",async e=>{e.preventDefault();msg("createError","");$("credentialBox").classList.add("hidden");try{const d=await api("/api/admin/clients","POST",{name:$("clientName").value.trim(),email:$("clientEmail").value.trim()});latestCredentials={email:d.email,password:d.password,client_id:d.engine.client_id,client_token:d.engine.client_token,server_url:d.engine_server_url};$("newEmail").textContent=d.email;$("newPassword").textContent=d.password;$("newClientId").textContent=d.engine.client_id;$("newClientToken").textContent=d.engine.client_token;$("newServerUrl").textContent=d.engine_server_url;$("credentialBox").classList.remove("hidden");$("createForm").reset();await refreshClients()}catch(x){msg("createError",x.message)}});
$("downloadCredentials").addEventListener("click",()=>{if(!latestCredentials)return;const blob=new Blob([JSON.stringify(latestCredentials,null,2)],{type:"application/json"});const a=document.createElement("a");a.href=URL.createObjectURL(blob);a.download="AL-ZOOL-MT5-Connection-Credentials.json";a.click();URL.revokeObjectURL(a.href)});
async function refreshResearch(){
  const target=$("researchSummary");if(!target)return;
  try{
    const result=await api("/api/ai/research");
    const runs=result.runs||[];
    if(!runs.length){target.textContent="لم تنفذ اختبارات تلقائية بعد؛ يبدأ التقييم عند وصول شموع جديدة.";return}
    target.textContent=runs.map(r=>{
      const p=r.payload||{},test=p.test||{},candidate=test.candidate||{};
      return String(r.created_at||"").slice(0,19)+" | "+(p.status||"UNKNOWN")+
        " | الشموع "+(p.candles??"—")+" | إشارات المرشح "+(candidate.signals??0)+
        " | تنشيط تلقائي: NO";
    }).join("\n");
  }catch(e){target.textContent="تعذر تحميل اختبارات التعلم: "+(e.message||"غير معروف")}
}
async function refreshVersions(){
  const host=$("versionList");if(!host)return;
  try{
    const data=await api("/api/ai/versions");
    host.replaceChildren();
    if(!data.versions?.length){host.textContent="لا توجد إصدارات محفوظة بعد.";return}
    for(const v of data.versions){
      const card=document.createElement("article");card.className="client-row";
      const info=document.createElement("div");const title=document.createElement("b");
      title.textContent=v.version_label+" • "+v.status;
      const details=document.createElement("small");
      const test=v.test_summary||{},candidate=test.candidate||{};
      details.textContent="الإنشاء: "+String(v.created_at||"").slice(0,19)+
        " | إشارات الاختبار: "+(candidate.signals??"—")+
        " | دقة الاتجاه: "+(candidate.signals?Math.round((candidate.hit_rate||0)*100)+"%":"غير كافية");
      info.append(title,details);card.append(info);
      if(v.status==="CANDIDATE"||v.status==="ARCHIVED"){
        const btn=document.createElement("button");btn.className="btn secondary small";
        btn.type="button";btn.textContent="اختيار هذا الإصدار";
        btn.onclick=async()=>{
          if(!confirm("تغيير أوزان الاستراتيجيات إلى "+v.version_label+"؟\nلن يتم تفعيل التداول. ويمكن الرجوع إلى إصدار سابق."))return;
          btn.disabled=true;
          try{await api("/api/ai/versions/activate","POST",{id:v.id});await refreshVersions()}
          catch(e){msg("versionError",e.message||"فشل تغيير الإصدار");btn.disabled=false}
        };
        card.append(btn);
      }
      host.append(card);
    }
  }catch(e){msg("versionError",e.message||"تعذر تحميل الإصدارات")}
}
$("refreshVersionsBtn").addEventListener("click",()=>{msg("versionError","");refreshVersions();refreshResearch()});
$("runExperimentBtn").addEventListener("click",async()=>{
  msg("versionError","");
  const btn=$("runExperimentBtn");btn.disabled=true;
  $("versionTestResult").textContent="جاري التقييم التاريخي دون إرسال أي أمر إلى MT5...";
  try{
    const d=await api("/api/ai/versions/test","POST",{});
    const x=d.test_summary||{},b=x.baseline||{},c=x.candidate||{};
    $("versionTestResult").textContent="النسخة المقترحة: "+d.version_label+
      " | إشارات الأساس: "+(b.signals??0)+" | إصابات: "+(b.hits??0)+
      " | إشارات المرشح: "+(c.signals??0)+" | إصابات: "+(c.hits??0)+
      " | لا تنشيط تلقائي؛ النتيجة ليست اختبار أرباح";
    await refreshVersions();
  }catch(e){msg("versionError",e.message||"تعذر تشغيل الاختبار")}
  finally{btn.disabled=false}
});
async function refreshActionControl(){
  try{
    const d=await api("/api/ai/actions/control");
    $("centralActionsStatus").textContent="إدارة الصفقات المركزية: "+(d.enabled?"ON":"OFF")+
      " | التداول المركزي: "+(d.trading_enabled?"ON":"OFF")+
      " | تفعيل MT5 منفصل وإلزامي";
  }catch(e){msg("centralActionsError",e.message||"تعذر قراءة حالة الإدارة")}
}
$("enableCentralActionsBtn").addEventListener("click",async()=>{
  msg("centralActionsError","");
  if(!confirm("تفعيل إدارة الصفقات المركزية قد يتيح فتح صفقات حقيقية عند تشغيل التداول وMT5. يجب التأكد من اللوت والخسارة والمجلس أولاً. هل تؤكد؟"))return;
  const btn=$("enableCentralActionsBtn");btn.disabled=true;
  try{await api("/api/ai/actions/control","POST",{enabled:true,confirmed_risk:true});
    await refreshActionControl()}
  catch(e){msg("centralActionsError",e.message||"فشل التفعيل")}
  finally{btn.disabled=false}
});
$("disableCentralActionsBtn").addEventListener("click",async()=>{
  msg("centralActionsError","");
  const btn=$("disableCentralActionsBtn");btn.disabled=true;
  try{await api("/api/ai/actions/control","POST",{enabled:false});
    await refreshActionControl()}
  catch(e){msg("centralActionsError",e.message||"فشل الإيقاف")}
  finally{btn.disabled=false}
});
async function refreshActions(){
  const target=$("actionsSummary");if(!target)return;
  try{
    const d=await api("/api/ai/actions");
    const lines=["حالة التنفيذ: "+(d.execution_authorized?"AUTHORIZED":"DIAGNOSTIC ONLY"),
      "القرار BUY: "+(d.buy||"HOLD"),"القرار SELL: "+(d.sell||"HOLD"),
      "السبب: "+(d.reason_ar||"—")];
    for(const a of d.actions||[]){
      lines.push(a.side+" | "+a.action+" | أقصى لوت مقترح "+a.lot_cap+
        " | ATR "+a.atr_distance+" | P/L "+a.floating_net+" | "+a.reason_ar);
    }
    target.textContent=lines.join("\n");
  }catch(e){target.textContent="فشل تحميل إدارة الصفقات: "+(e.message||"غير معروف")}
}
$("refreshActionsBtn").addEventListener("click",refreshActions);
async function refreshLearning(){
  const target=$("learningSummary");if(!target)return;
  try{
    const d=await api("/api/ai/learning");
    const lines=["حالة الذاكرة: "+(d.status||"WAIT"),
      "الصفقات المخزنة: "+(d.memory_count??0),
      "الصفقات المقيمة: "+(d.processed_positions??0),
      "تغطية ربط الاستراتيجيات: "+(d.coverage_percent??0)+"%",
      "ربح/خسارة: "+(d.wins??0)+" / "+(d.losses??0)];
    for(const row of (d.top_strategies||[]).slice(0,5)){
      lines.push("استراتيجية "+row.strategy_id+": "+row.trades+" صفقة | صافي "+Number(row.net||0).toFixed(2));
    }
    target.textContent=lines.join("\n");
  }catch(e){target.textContent="تعذر تحميل التعلم: "+(e.message||"غير معروف")}
}
$("refreshLearningBtn").addEventListener("click",refreshLearning);
async function refreshCouncil(){
  const target=$("councilSummary");if(!target)return;
  try{
    const data=await api("/api/ai/council");
    if(!data.connected){target.textContent="MT5 غير متصل — المجلس لا يصدر إشارات";return}
    const c=data.council||{},votes=c.votes||{},risk=data.risk_guardian||{};
    const lines=["الشمعة: "+(data.bar_time||"—"),"قرار المحرك: "+(data.signal||"WAIT"),
      "موافقة المجلس: "+(c.approved?"APPROVED":"WAIT"),
      "السبب: "+(c.reason_ar||"—")];
    for(const name of ["Fast AI","Qwen","DeepSeek"]){
      const v=votes[name]||{};lines.push(name+": "+(v.status||"PENDING")+" | "+(v.direction||"WAIT")+
        " | "+(Math.round(Number(v.confidence||0)*100))+"% | "+(v.reason_ar||""));
    }
    lines.push("Risk Guardian: "+(risk.approved?"PASS":"BLOCK")+" | "+(risk.reason_ar||""));
    target.textContent=lines.join("\n");
  }catch(e){target.textContent="فشل تحميل التشخيص: "+(e.message||"غير معروف")}
}
$("refreshCouncilBtn").addEventListener("click",refreshCouncil);
async function refreshConnection(client=false){try{const d=await api("/api/connection/status");const connected=d.connected;const html=connected?'<span class="pill online">متصل</span>':'<span class="pill offline">غير متصل</span>';const account=d.connection?.account||{};const details=html+(connected?'<div class="connection-meta">الحساب: '+safe(account.login)+' • الخادم: '+safe(account.server)+' • الرمز: '+safe(account.symbol)+'</div><small>آخر نبضة: '+safe(d.connection.last_seen)+'</small>':'<div class="muted">لم تصل نبضة اتصال خلال آخر 30 ثانية.</div>');const target=$(client?"clientConnection":"connectionDetails");if(target)target.innerHTML=details;$("connection").textContent=connected?"MT5 متصل":"MT5 غير متصل"}catch(e){$("connection").textContent="تعذر قراءة حالة MT5"}}
function safe(v){return String(v??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]))}
setInterval(()=>{if(session){if(!$("adminView").classList.contains("hidden")){refreshConnection();refreshAdminOverview()}else if(!$("clientView").classList.contains("hidden"))refreshConnection(true)}},20000);
boot();