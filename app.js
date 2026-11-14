let sb=null,session=null,latestCredentials=null,supabaseReady=false;
const $=id=>document.getElementById(id);
async function api(path,method="GET",body=null){const headers={"Content-Type":"application/json"};if(session?.access_token)headers.Authorization="Bearer "+session.access_token;const r=await fetch(path,{method,headers,body:body?JSON.stringify(body):null});const d=await r.json().catch(()=>({}));if(!r.ok)throw new Error(d.error||"تعذر تنفيذ الطلب");return d}
function show(id){["loginView","adminView","clientView"].forEach(x=>$(x).classList.toggle("hidden",x!==id))}
function msg(id,value){$(id).textContent=value||""}
async function boot(){try{const response=await fetch("/api/config",{cache:"no-store"});if(!response.ok)throw Error("تعذر قراءة إعدادات الخادم ("+response.status+")");const c=await response.json();if(!c.ready){const missing=Array.isArray(c.missing)?c.missing:[];throw Error("إعدادات Supabase غير مكتملة على Render"+(missing.length?" — المتغيرات الناقصة: "+missing.join(", "):" — راجع متغيرات البيئة"));}if(!window.supabase||typeof window.supabase.createClient!=="function")throw Error("تعذر تحميل مكتبة Supabase؛ حدّث الصفحة أو تحقق من الاتصال.");if(!c.supabase_url||!c.supabase_anon_key)throw Error("رابط Supabase أو المفتاح العام غير موجود.");sb=window.supabase.createClient(c.supabase_url,c.supabase_anon_key);if(!sb?.auth)throw Error("تعذر تهيئة تسجيل الدخول عبر Supabase.");supabaseReady=true;const {data,error}=await sb.auth.getSession();if(error)throw error;session=data.session;if(session)await route();else show("loginView");}catch(e){supabaseReady=false;sb=null;$("connection").textContent="الخدمة تحتاج إعداد";msg("loginError",e.message||"تعذر تهيئة Supabase");show("loginView");}}
async function route(){const a=await api("/api/access/check");if(a.role==="owner"){show("adminView");$("ownerLabel").textContent=a.email;await refreshClients();await refreshConnection()}else{show("clientView");$("clientLabel").textContent=a.email;await refreshConnection(true)}}
$("loginForm").addEventListener("submit",async e=>{e.preventDefault();msg("loginError","");if(!supabaseReady||!sb?.auth){msg("loginError","تسجيل الدخول متوقف لأن إعدادات Supabase لم تكتمل بعد. راجع رسالة إعداد الخادم أعلاه.");return;}try{const {data,error}=await sb.auth.signInWithPassword({email:$("loginEmail").value.trim(),password:$("loginPassword").value});if(error)throw error;session=data.session;await route()}catch(x){msg("loginError",x.message||"تعذر تسجيل الدخول")}});
async function logout(){await sb.auth.signOut();session=null;show("loginView")}
$("logoutBtn").addEventListener("click",logout);$("clientLogoutBtn").addEventListener("click",logout);
async function refreshClients(){const d=await api("/api/admin/clients");$("clientCount").textContent=d.clients.length;$("activeCount").textContent=d.clients.filter(x=>x.active).length;const host=$("clientsList");host.replaceChildren();if(!d.clients.length){host.textContent="لا يوجد عملاء حالياً.";return}for(const c of d.clients){const card=document.createElement("article");card.className="client-row";const info=document.createElement("div");const name=document.createElement("b");name.textContent=c.name||c.email;const meta=document.createElement("small");meta.textContent=c.email+" • "+(c.licensed?"ترخيص موجود":"لا يوجد ترخيص");info.append(name,meta);const actions=document.createElement("div");actions.className="actions";const toggle=document.createElement("button");toggle.className="btn small "+(c.active?"ghost":"secondary");toggle.textContent=c.active?"إيقاف":"تفعيل";toggle.onclick=async()=>{try{await api("/api/admin/clients/"+c.id,"PATCH",{active:!c.active});await refreshClients()}catch(e){alert(e.message)}};const del=document.createElement("button");del.className="btn danger small";del.textContent="حذف";del.onclick=async()=>{if(!confirm("حذف حساب هذا العميل؟"))return;try{await api("/api/admin/clients/"+c.id,"DELETE");await refreshClients()}catch(e){alert(e.message)}};actions.append(toggle,del);card.append(info,actions);host.append(card)}}
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
async function refreshConnection(client=false){try{const d=await api("/api/connection/status");const connected=d.connected;const html=connected?'<span class="pill online">متصل</span>':'<span class="pill offline">غير متصل</span>';const account=d.connection?.account||{};const details=html+(connected?'<div class="connection-meta">الحساب: '+safe(account.login)+' • الخادم: '+safe(account.server)+' • الرمز: '+safe(account.symbol)+'</div><small>آخر نبضة: '+safe(d.connection.last_seen)+'</small>':'<div class="muted">لم تصل نبضة اتصال خلال آخر 30 ثانية.</div>');const target=$(client?"clientConnection":"connectionDetails");if(target)target.innerHTML=details;$("connection").textContent=connected?"MT5 متصل":"MT5 غير متصل"}catch(e){$("connection").textContent="تعذر قراءة حالة MT5"}}
function safe(v){return String(v??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]))}
setInterval(()=>{if(session){if(!$("adminView").classList.contains("hidden"))refreshConnection();else if(!$("clientView").classList.contains("hidden"))refreshConnection(true)}},10000);
boot();