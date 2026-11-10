let sb=null,session=null,latestCredentials=null;
const $=id=>document.getElementById(id);
let lastApiRoundTripMs=null;
async function api(path,method="GET",body=null){const headers={"Content-Type":"application/json"};if(session&&session.access_token)headers.Authorization="Bearer "+session.access_token;const started=Date.now();const r=await fetch(path,{method,headers,body:body?JSON.stringify(body):undefined,cache:method==="GET"?"no-store":"default"});lastApiRoundTripMs=Date.now()-started;const d=await r.json().catch(()=>({}));if(!r.ok)throw new Error(d.error||d.msg||"تعذر تنفيذ الطلب");return d}
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
 ["home","الرئيسية","⌂"],["connection","ملخص الحساب والاتصال","◉"],["control","التحكم بالتداول","⏻"],["gold","الذهب والاستراتيجيات","◆"],["indicator","المؤشر الصناعي","✧"],["candles","الشموع","▥"],["council","مجلس التصويت","◎"],["aiCouncil","مجلس الذكاء الاصطناعي","✦"],["analysis","التحليل الذكي","⌁"],["lab","مختبر الفرص","⌕"],["families","عائلات الاستراتيجيات","▦"],["strategies","الاستراتيجيات والتركيبات","▤"],["performance","أداء الاستراتيجيات","↗"],["trades","الصفقات والأداء","↗"],["history","سجل الصفقات","▤"],["open","الصفقات المفتوحة","◫"],["pending","الأوامر المعلقة والسجل","◷"],["daily","الأداء اليومي","▧"],["risk","مراقبة المخاطر","⬡"],["results","نتائج الذكاء الاصطناعي","✧"],["learning","التعلم والتكيف","⟳"],["logs","السجلات","≡"],["signals","سجل الإشارات","◉"],["blackbox","AZ BLACK BOX","⬡"],["settings","حالة الحساب","⚙"],["settings_profit","إعدادات الأرباح والخسائر","＄"],["settings_lot","إعدادات اللوت والدخول","◈"],["settings_protection","إعدادات الحماية والتريلينغ","⛨"],["settings_addon","إعدادات التعزيز والتبريد","＋"],["settings_candle","إعدادات الشموع والانعكاس","▥"],["settings_strategy","إعدادات الاستراتيجيات","⌘"],["licenses","إدارة العملاء والتراخيص","♙"]];
const strategyFamilies=["EMA Trend","RSI Momentum","MACD Momentum","Bollinger Reversion","Donchian Breakout","ATR Expansion","Candle Momentum","EMA Pullback","Range Reversion","Hybrid Confluence","Stochastic Reversal","CCI Momentum","Williams %R Reversal","ROC Momentum","Adaptive Swing Trend","VWAP Deviation","Pivot Breakout","Inside Bar Breakout","Engulfing Reversal","Volume Pressure"];
let activeDashboardPage="home",dashboardRole="client",dashboardStatusTimer=null,tradingEnabled=false,mt5Connected=false;
let liveTelemetryTimer=null,liveTelemetryBusy=false;

const BASIC_SETTING_DEFAULTS={"lot":0.01,"ai_risk_percent":0.5,"ai_max_lot":0.05,"profit_target":1,"loss_limit":50,"global_profit_enabled":true,"per_trade_profit_enabled":true,"per_trade_profit_target":2,"smart_profit_enabled":false,"one_direction_only":true,"global_loss_enabled":true,"global_loss_amount":50,"ai_loss_guard_enabled":false,"max_positions":0,"fixed_sl_enabled":true,"fixed_sl_distance":3,"trailing_enabled":true,"trail_start":3,"trail_distance":3,"pending_distance":2,"order_mode":"Market Order","reverse_trigger":5,"reverse_continue":2,"candle_chase_enabled":false,"candle_chase_min_move":0.1,"candle_chase_cooldown":2,"candle_chase_buy_target":1,"candle_chase_sell_target":1,"candle_chase_single_target":0.5,"candle_chase_sl_enabled":false,"candle_chase_sl_distance":3,"addon_enabled":false,"addon_smart_enabled":false,"addon_step":2,"addon_multiplier_enabled":true,"addon_multiplier":1.2,"addon_max":3,"addon_max_lot":0.05,"cooling_enabled":false,"cooling_smart_enabled":false,"cooling_step":3,"cooling_multiplier_enabled":true,"cooling_multiplier":1.2,"cooling_max":2,"cooling_max_lot":0.05,"strategy_mode":"AUTO — Best current fit"};
const BASIC_SETTING_FIELDS=[["lot","حجم اللوت","number"],["ai_risk_percent","مخاطرة AI %","number"],["ai_max_lot","أقصى لوت AI","number"],["profit_target","هدف الربح الجماعي","number"],["per_trade_profit_target","هدف ربح الصفقة الواحدة","number"],["global_profit_enabled","تفعيل جني الربح الجماعي","bool"],["per_trade_profit_enabled","تفعيل جني ربح الصفقة الواحدة","bool"],["smart_profit_enabled","جني الأرباح الذكي","bool"],["loss_limit","حد الخسارة اليومي","number"],["global_loss_enabled","تفعيل حد الخسارة العام","bool"],["global_loss_amount","قيمة حد الخسارة العام","number"],["ai_loss_guard_enabled","حارس الخسارة الذكي","bool"],["one_direction_only","اتجاه واحد فقط","bool"],["max_positions","الحد الأقصى للصفقات (0 = بلا حد)","number"],["fixed_sl_enabled","تفعيل وقف الخسارة الثابت","bool"],["fixed_sl_distance","مسافة وقف الخسارة الثابت","number"],["trailing_enabled","تفعيل التريلينغ","bool"],["trail_start","بداية التريلينغ","number"],["trail_distance","مسافة التريلينغ","number"],["order_mode","طريقة الأوامر","select"],["pending_distance","مسافة الأمر المعلق","number"],["reverse_trigger","مسافة تفعيل الانعكاس","number"],["reverse_continue","تأكيد استمرار الانعكاس","number"],["addon_enabled","تفعيل التعزيز","bool"],["addon_smart_enabled","التعزيز الذكي حسب الشموع والاستراتيجية","bool"],["addon_step","مسافة التعزيز الأساسية","number"],["addon_multiplier_enabled","تفعيل مضاعف لوت التعزيز","bool"],["addon_multiplier","مضاعف لوت التعزيز","number"],["addon_max","الحد الأقصى للتعزيز","number"],["addon_max_lot","أقصى لوت للتعزيز","number"],["cooling_enabled","تفعيل التبريد","bool"],["cooling_smart_enabled","التبريد الذكي حسب الشموع والاستراتيجية","bool"],["cooling_step","مسافة التبريد الأساسية","number"],["cooling_multiplier_enabled","تفعيل مضاعف لوت التبريد","bool"],["cooling_multiplier","مضاعف لوت التبريد","number"],["cooling_max","الحد الأقصى للتبريد","number"],["cooling_max_lot","أقصى لوت للتبريد","number"],["candle_chase_enabled","تفعيل متابعة الشموع","bool"],["candle_chase_min_move","أقل حركة لمتابعة الشمعة","number"],["candle_chase_cooldown","فترة تهدئة متابعة الشموع","number"],["candle_chase_buy_target","هدف متابعة الشراء","number"],["candle_chase_sell_target","هدف متابعة البيع","number"],["candle_chase_single_target","هدف الصفقة الواحدة لمتابعة الشموع","number"],["candle_chase_sl_enabled","وقف خسارة متابعة الشموع","bool"],["candle_chase_sl_distance","مسافة وقف متابعة الشموع","number"],["strategy_mode","اختيار الاستراتيجية","strategy"]];
let basicStrategyCatalog=[];
function renderBasicSettings(cfg,groupId){
 const host=$("basicSettingsForm");if(!host)return;
 const groups={
  settings_profit:["إدارة الأرباح والخسائر",["profit_target","per_trade_profit_target","global_profit_enabled","per_trade_profit_enabled","smart_profit_enabled","loss_limit","global_loss_enabled","global_loss_amount","ai_loss_guard_enabled"]],
  settings_lot:["اللوت والدخول",["lot","ai_risk_percent","ai_max_lot","one_direction_only","max_positions","order_mode","pending_distance"]],
  settings_protection:["الحماية والتريلينغ",["fixed_sl_enabled","fixed_sl_distance","trailing_enabled","trail_start","trail_distance"]],
  settings_addon:["التعزيز والتبريد",["addon_enabled","addon_smart_enabled","addon_step","addon_multiplier_enabled","addon_multiplier","addon_max","addon_max_lot","cooling_enabled","cooling_smart_enabled","cooling_step","cooling_multiplier_enabled","cooling_multiplier","cooling_max","cooling_max_lot"]],
  settings_candle:["متابعة الشموع والانعكاس",["candle_chase_enabled","candle_chase_min_move","candle_chase_cooldown","candle_chase_buy_target","candle_chase_sell_target","candle_chase_single_target","candle_chase_sl_enabled","candle_chase_sl_distance","reverse_trigger","reverse_continue"]],
  settings_strategy:["الاستراتيجيات",["strategy_mode"]]
 };
 const group=groups[groupId]||groups.settings_profit,fieldMap=Object.fromEntries(BASIC_SETTING_FIELDS.map(x=>[x[0],x]));
 host.innerHTML='<section class="basic-setting-group"><h4>'+esc(group[0])+'</h4><div class="basic-setting-grid">'+group[1].map(k=>{const f=fieldMap[k],v=cfg[k]??BASIC_SETTING_DEFAULTS[k];if(f[2]==="bool")return '<label class="basic-setting-toggle"><input type="checkbox" data-setting="'+k+'" '+(v===true?'checked':'')+'><span>'+esc(f[1])+'</span></label>';if(f[2]==="strategy"){const opts=[{value:'AUTO — Best current fit',label:'AUTO — Best current fit'},{value:'HUNTER — Frequent opportunities',label:'HUNTER — Frequent opportunities'},{value:'SNIPER — Selective entries',label:'SNIPER — Selective entries'},...(basicStrategyCatalog.length?basicStrategyCatalog:Array.from({length:200},(_,i)=>({value:String(i+1).padStart(3,"0"),label:'#'+String(i+1).padStart(3,"0")})))];return '<label class="basic-setting-field"><span>'+esc(f[1])+'</span><select data-setting="'+k+'">'+opts.map(o=>'<option value="'+esc(o.value)+'" '+(String(v)===o.value?'selected':'')+'>'+esc(o.label)+'</option>').join('')+'</select></label>';}if(f[2]==="select")return '<label class="basic-setting-field"><span>'+esc(f[1])+'</span><select data-setting="'+k+'"><option '+(v==="Market Order"?"selected":"")+' value="Market Order">Market Order</option><option '+(v==="Pending Order"?"selected":"")+' value="Pending Order">Pending Order</option></select></label>';return '<label class="basic-setting-field"><span>'+esc(f[1])+'</span><input type="text" inputmode="decimal" dir="ltr" autocomplete="off" data-setting="'+k+'" value="'+esc(v)+'"></label>'}).join('')+'</div></section><div class="basic-setting-actions"><button id="saveBasicSettings" class="dash-btn">حفظ هذا القسم</button><span id="basicSettingsMessage" class="muted"></span></div>';
 $("saveBasicSettings").addEventListener("click",()=>saveBasicSettings(groupId));
}
async function loadBasicSettings(groupId){
 const [d,c]=await Promise.all([api("/api/engine/settings"),api("/api/engine/catalog").catch(()=>({strategies:[]}))]);
 basicStrategyCatalog=(c.strategies||[]).map(x=>({value:String(x.id).padStart(3,"0"),label:'#'+String(x.id).padStart(3,"0")+' '+x.name+' — '+x.family}));
 renderBasicSettings({...BASIC_SETTING_DEFAULTS,...(d.settings||{})},groupId);
}
function normalizeNumericInput(raw){
 let value=String(raw??"").trim();
 value=value.replace(/[٠-٩]/g,ch=>String(ch.charCodeAt(0)-1632));
 value=value.replace(/[۰-۹]/g,ch=>String(ch.charCodeAt(0)-1776));
 value=value.replace(/[٫]/g,".").replace(/[٬،]/g,"");
 value=value.replace(/\s+/g,"");
 if(value.includes(",")&&!value.includes("."))value=value.replace(",",".");
 else value=value.replace(/,/g,"");
 return value;
}
async function saveBasicSettings(groupId){
 const host=$("basicSettingsForm"),msg=$("basicSettingsMessage");if(!host)return;
 const values={};let invalid=null;
 host.querySelectorAll("[data-setting]").forEach(el=>{
  const k=el.dataset.setting;
  if(el.type==="checkbox"){values[k]=el.checked;return}
  if(el.tagName==="SELECT"){values[k]=el.value;return}
  const normalized=normalizeNumericInput(el.value),number=Number(normalized);
  if(normalized===""||!Number.isFinite(number)){invalid=el;return}
  values[k]=number;
 });
 if(invalid){msg.textContent="أدخل رقماً صحيحاً في الحقل المحدد.";invalid.focus();return}
 msg.textContent="جارٍ الحفظ...";
 try{const d=await api("/api/engine/settings","POST",values);renderBasicSettings({...BASIC_SETTING_DEFAULTS,...d.settings},groupId);$("basicSettingsMessage").textContent="تم حفظ هذا القسم."; }catch(e){msg.textContent=e.message;}
}

function dashboardPageBody(id){
 if(id==="connection"||id==="aiCouncil"||id==="performance"||id==="risk")return '<div class="dash-panel"><h3>'+({connection:"مراقبة الاتصال والسرعة",aiCouncil:"مجلس الذكاء الاصطناعي",performance:"أداء الاستراتيجيات",risk:"مراقبة المخاطر"}[id])+'</h3><p class="muted">يتم تحديث هذه الصفحة من بيانات الحساب والمحرك الفعلية.</p></div>';
 if(id==="licenses")return '<div class="dash-panel"><h3>إدارة العملاء والتراخيص</h3><div id="licenseClientsList" class="catalog-list">جاري تحميل العملاء...</div></div>';

 const wait='<div class="engine-wait"><span class="wait-icon">⌁</span><b>بانتظار ربط محرك التداول</b><p>هذه الصفحة جاهزة ضمن لوحة التحكم. ستظهر البيانات الحقيقية بعد نشر المحرك المركزي وربط موصل MT5 والتحقق منه.</p><small>لا توجد بيانات سوق أو صفقات تجريبية معروضة على أنها حقيقية.</small></div>';
 if(id==="home")return '<div class="dash-cards"><article><small>اتصال المحرك</small><b id="engineHomeStatus" class="state-off">بانتظار الإعداد</b></article><article><small>اتصال MT5</small><b id="mt5HomeStatus" class="state-off">جارٍ التحقق...</b></article><article><small>الرمز</small><b>XAUUSD</b></article><article><small>حالة التداول</small><b id="tradeHomeStatus" class="state-off">متوقف</b></article></div><div class="dash-panel"><h3>ملخص المنصة</h3><p>تم تجهيز صفحات البوت داخل حسابك. لم يتم تفعيل أوامر شراء أو بيع من الويب بعد.</p>'+wait+'</div>';
 if(id==="families")return '<div class="dash-panel"><h3>عائلات الاستراتيجيات الأساسية</h3><p>يتم تحميل القائمة مباشرة من محرك الاستراتيجيات الأصلي.</p><div id="familyCatalog" class="family-grid"><p class="muted">جاري تحميل العائلات...</p></div><p class="muted">وجود العائلة في الكتالوج لا يعني أنها اجتازت اختبار ربحية.</p></div>';
 if(id==="strategies")return '<div class="dash-panel"><h3>كتالوج المحرك الأصلي</h3><div id="catalogStats" class="dash-cards"><article><small>الاستراتيجيات</small><b>—</b></article><article><small>التركيبات</small><b>—</b></article><article><small>العائلات</small><b>—</b></article></div><h4>الاستراتيجيات الـ200</h4><div id="strategyCatalog" class="catalog-list">جاري تحميل الاستراتيجيات...</div><details class="combo-details"><summary>عرض التركيبات الـ1000</summary><div id="combinationCatalog" class="catalog-list">جاري تحميل التركيبات...</div></details><p class="muted">هذا كتالوج المحرك الحقيقي. التقييم الحي يحتاج بيانات شموع من MT5.</p></div>';
 if(id==="control")return '<div class="dash-panel"><h3>التحكم بالتداول</h3><div class="trade-lock"><b id="tradeControlStatus">التداول متوقف افتراضياً</b><p>التشغيل يسمح لموصل MT5 بتنفيذ إشارات BUY/SELL الجديدة. يقرأ موصل MT5 الإعدادات الأساسية المحفوظة في حسابك، ويطبق حدود اللوت والحماية وجني الأرباح والأوامر المعلقة والتعزيز والتبريد والاستراتيجية المختارة. لا يبدأ التنفيذ إلا بعد تأكيد الاتصال والقرار وتفعيل التداول من هذه اللوحة.</p><div class="trade-actions"><button id="enableTradingBtn" class="dash-btn" disabled>بدء التداول</button><button id="disableTradingBtn" class="dash-btn" disabled>إيقاف التداول</button></div><small id="tradeControlNote">جارٍ التحقق من اتصال MT5...</small></div></div>';
 if(id==="signals")return '<div class="dash-panel"><div class="row-head"><h3>سجل الإشارات والتنفيذ</h3><button id="refreshSignals" class="btn ghost small">تحديث الآن</button></div><p class="muted">يسجل قرار المحرك وأسبابه، ثم تأكيدات التنفيذ الفعلية التي تصل من MT5. ظهور الإشارة لا يعني أن الصفقة فُتحت.</p><label class="basic-setting-field">تصفية السجل<select id="signalFilter"><option value="ALL">كل الأحداث</option><option value="SIGNAL_CREATED">إشارات شراء/بيع</option><option value="WAIT">انتظار</option><option value="BLOCKED">منع الدخول</option><option value="ORDER_SENT">تم إرسال الأمر</option><option value="TRADE_OPENED">تم فتح الصفقة</option><option value="TRADE_CLOSED">إغلاق الصفقة</option><option value="PROTECTION">حماية</option><option value="ORDER_REJECTED">رفض الأمر</option><option value="ADDON_OPENED">تعزيز</option><option value="COOLING_OPENED">تبريد</option></select></label><div id="signalEventsList" class="catalog-list"><p class="muted">جارٍ تحميل سجل الإشارات...</p></div><small class="muted">التحديث التلقائي كل 5 ثوانٍ. تأكيد فتح الصفقة يأتي من تنفيذ الوسيط في MT5.</small></div>';
 if(id.startsWith("settings_"))return '<div class="dash-panel"><h3>إعدادات مستقلة</h3><p class="muted">يتم حفظ حقول هذا القسم فقط، مع الاحتفاظ ببقية إعدادات الحساب دون تغيير.</p><div id="basicSettingsForm" class="basic-settings"><p class="muted">جاري تحميل الإعدادات...</p></div></div>';
 if(id==="settings")return '<div class="dash-panel"><h3>حالة الحساب والمحرك</h3><p class="muted">بيانات الحساب الفعلية وحالة MT5. استخدم صفحات الإعدادات المنفصلة لتعديل كل مجموعة.</p><div id="engineSettingsLive"><p class="muted">جارٍ تحميل البيانات الفعلية...</p></div></div>';
 const titles={connection:"مراقبة الاتصال والسرعة",aiCouncil:"مجلس الذكاء الاصطناعي",performance:"أداء الاستراتيجيات",risk:"مراقبة المخاطر",gold:"الذهب والاستراتيجيات",indicator:"المؤشر الصناعي اللحظي",candles:"الشموع وحركة السوق",council:"مجلس التصويت",analysis:"التحليل الذكي",lab:"مختبر الفرص",trades:"الصفقات والأداء",history:"سجل الصفقات",open:"الصفقات المفتوحة",pending:"الأوامر المعلقة والسجل",daily:"الأداء اليومي",results:"نتائج الذكاء الاصطناعي",learning:"التعلم والتكيف",logs:"السجلات",blackbox:"AZ BLACK BOX",licenses:"إدارة العملاء والتراخيص"};
 return '<div class="dash-panel"><h3>'+titles[id]+'</h3>'+wait+'</div>';
}
function renderDashboard(){
 dashboardRoot.innerHTML='<div class="dash-top"><div><div class="dash-brand">AL-ZOOL <span>GOLD SNIPER</span></div><small>مركز التداول الذكي • XAUUSD</small></div><div class="dash-top-actions"><span class="engine-pill">المحرك: غير مربوط</span><button id="dashLogout" class="btn ghost">تسجيل الخروج</button></div></div><div class="dash-layout"><aside class="dash-side"><div class="dash-user">'+(dashboardRole==="owner"?"OWNER CONTROL":"CLIENT WORKSPACE")+'</div>'+navPages.filter(p=>p[0]!=="licenses"||dashboardRole==="owner").map(p=>'<button class="dash-nav '+(p[0]===activeDashboardPage?"selected":"")+'" data-page="'+p[0]+'"><span>'+p[2]+'</span>'+p[1]+'</button>').join("")+(dashboardRole==="owner"?'<button class="dash-nav back-admin" data-page="admin">↩ إدارة العملاء</button>':"")+'</aside><main class="dash-main"><div class="dash-heading"><div><small>لوحة التحكم</small><h1>'+(navPages.find(p=>p[0]===activeDashboardPage)||navPages[0])[1]+'</h1></div><span id="engineMarketTag" class="market-tag">XAUUSD • بانتظار تهيئة المحرك</span></div><section id="livePageData" class="live-page-data"></section>'+dashboardPageBody(activeDashboardPage)+'</main></div>';
 $("dashLogout").addEventListener("click",logout);
 if($("enableTradingBtn"))$("enableTradingBtn").addEventListener("click",()=>setTradingEnabled(true));
 if($("disableTradingBtn"))$("disableTradingBtn").addEventListener("click",()=>setTradingEnabled(false));
 dashboardRoot.querySelectorAll("[data-page]").forEach(b=>b.addEventListener("click",()=>{if(b.dataset.page==="admin"){show("adminView");return}activeDashboardPage=b.dataset.page;renderDashboard()}));
 if(activeDashboardPage==="families"||activeDashboardPage==="strategies")loadEngineCatalog();
 if(activeDashboardPage.startsWith("settings_"))loadBasicSettings(activeDashboardPage).catch(e=>{const h=$("basicSettingsForm");if(h)h.innerHTML="<p class=error>"+esc(e.message)+"</p>"});
 if(dashboardStatusTimer)clearInterval(dashboardStatusTimer);
 refreshEngineStatus();
 loadDashboardWorkspace();
 dashboardStatusTimer=setInterval(()=>{if(!$("dashboardView")?.classList.contains("hidden")){refreshEngineStatus();loadDashboardWorkspace()}},5000);
 if(liveTelemetryTimer)clearInterval(liveTelemetryTimer);
 refreshLiveTelemetry();
 liveTelemetryTimer=setInterval(()=>{if(!$("dashboardView")?.classList.contains("hidden"))refreshLiveTelemetry()},500);
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
function show(v){["loginView","adminView","clientView","dashboardView"].forEach(id=>{const el=$(id);if(el)el.classList.toggle("hidden",id!==v)});document.body.classList.toggle("owner-theme",v==="adminView"||(v==="dashboardView"&&dashboardRole==="owner"));document.body.classList.toggle("client-theme",v==="dashboardView"&&dashboardRole==="client");}
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

async function refreshLiveTelemetry(){
 if(liveTelemetryBusy||!session)return;
 liveTelemetryBusy=true;
 try{
  const d=await api("/api/engine/live"),state=d.live_state||{},account=state.account||{},tick=state.tick||{},candle=state.forming_candle||{},indicator=d.indicator||{},positions=state.positions||[],orders=state.orders||[];
  const pageHost=$("livePageData"),settingsHost=$("engineSettingsLive");
  const money=v=>Number.isFinite(Number(v))?Number(v).toFixed(2):"—";
  const escv=v=>esc(v==null?"—":v);
  const floating=positions.reduce((sum,p)=>sum+Number(p.profit||0)+Number(p.swap||0),0);
  const tone=v=>Number(v)>0?"open-pnl-positive":Number(v)<0?"open-pnl-negative":"open-pnl-zero";
  const pnlMarkup=(v,currency)=>'<strong class="'+tone(v)+'">'+esc(money(v))+' '+esc(currency||"")+'</strong>';
  const received=d.received_at?new Date(d.received_at).toLocaleString("ar-AE"):"لم يصل بعد";
  const status=d.connected?(Number(d.age_ms)<=1500?"لحظي":"متصل — البيانات متأخرة"):"غير متصل";
  const accountPage='<div class="dash-cards">'+[
   ["حالة MT5",status],["الرصيد",money(account.balance)+" "+(account.currency||"")],["Equity",money(account.equity)+" "+(account.currency||"")],
   ["الهامش المستخدم",money(account.margin)],["الهامش المتاح",money(account.free_margin)],["الصفقات المفتوحة",positions.length],
   ["الربح العائم",pnlMarkup(floating,account.currency)],["Bid / Ask",money(tick.bid)+" / "+money(tick.ask)],
   ["عمر بيانات MT5",d.age_ms==null?"—":d.age_ms+" ms"],["آخر وصول",received]
  ].map(v=>'<article><small>'+esc(v[0])+'</small><b>'+(v[0]==="الربح العائم"?v[1]:esc(v[1]))+'</b></article>').join("")+'</div><p class="muted">هذه الصفحة الوحيدة لملخص الحساب والاتصال وبيانات السوق العامة.</p>';
  const positionRows=positions.length?'<div class="catalog-list">'+positions.map(p=>{
   const pnl=Number(p.profit||0)+Number(p.swap||0),side=Number(p.type)===0?"شراء":"بيع";
   return '<div class="catalog-row open-position-row"><b>'+esc(p.symbol||state.symbol||"XAUUSD")+' • '+side+' • '+esc(p.volume)+' لوت</b><small>#'+esc(p.ticket)+' • الدخول '+esc(p.open_price)+' • السعر الحالي '+esc(p.price_current)+'</small><small>الربح / الخسارة: '+pnlMarkup(pnl,account.currency)+' • SL '+esc(p.sl)+' • TP '+esc(p.tp)+'</small></div>';
  }).join("")+'</div>':'<p class="muted">لا توجد صفقات مفتوحة حالياً.</p>';
  const openPage='<div class="open-floating-summary"><span>إجمالي الربح العائم</span>'+pnlMarkup(floating,account.currency)+'</div>'+positionRows;
  const orderRows=orders.length?'<div class="catalog-list">'+orders.map(o=>'<div class="catalog-row"><b>'+esc(o.symbol||state.symbol||"XAUUSD")+' • '+esc(o.type)+' • '+esc(o.volume)+' لوت</b><small>#'+esc(o.ticket)+' • السعر '+esc(o.price)+' • SL '+esc(o.sl)+' • TP '+esc(o.tp)+' • '+esc(o.state)+'</small></div>').join("")+'</div>':'<p class="muted">لا توجد أوامر معلقة حالياً.</p>';
  const op=Number(candle.open),price=Number(tick.bid||candle.close),hi=Math.max(Number(candle.high),op,price),lo=Math.min(Number(candle.low),op,price),span=Math.max(hi-lo,0.0000001),top=Math.max(0,Math.min(98,(hi-Math.max(op,price))/span*100)),height=Math.max(2,Math.min(100-top,Math.abs(price-op)/span*100)),candleColor=price>=op?"#db62b5":"#ef4444";
  const candlePage=candle.time?'<div class="live-candle-card"><b>الشمعة الحالية</b><div style="height:132px;display:flex;align-items:center;justify-content:center;gap:14px"><div style="position:relative;width:34px;height:112px"><div style="position:absolute;left:16px;top:0;width:2px;height:100%;background:#9ca3af"></div><div style="position:absolute;left:7px;top:'+top+'%;width:20px;height:'+height+'%;min-height:3px;background:'+candleColor+';border-radius:3px"></div></div><div><b style="color:'+candleColor+'">'+money(price)+'</b><small style="display:block">السعر الحالي من MT5</small></div></div></div><div class="dash-cards">'+[["الافتتاح",money(candle.open)],["الأعلى",money(hi)],["الأدنى",money(lo)],["الإغلاق الحالي",money(price)],["حجم التيك",candle.tick_volume??"—"]].map(v=>'<article><small>'+esc(v[0])+'</small><b>'+esc(v[1])+'</b></article>').join("")+'</div>':'<p class="muted">لا توجد شمعة حالية مستلمة من MT5.</p>';
  const indicatorPage='<div class="dash-panel ai-indicator-panel"><h4>المؤشر الصناعي — قراءة مساعدة فقط</h4>'+(indicator.ready?'<div class="dash-cards">'+[["الاتجاه",indicator.direction==="BUY"?"شراء":indicator.direction==="SELL"?"بيع":"محايد"],["القوة",money(indicator.strength)+"/100"],["قوة الشراء",money(indicator.buy_score)+"/100"],["قوة البيع",money(indicator.sell_score)+"/100"],["RSI",indicator.components?.rsi??"—"],["قوة الاتجاه",indicator.components?.trend??"—"],["ضغط الشمعة",indicator.components?.forming_candle??"—"]].map(v=>'<article><small>'+esc(v[0])+'</small><b>'+esc(v[1])+'</b></article>').join("")+'</div><p>'+esc(indicator.reason_ar||"")+'</p>':'<p class="muted">بانتظار بيانات تحليلية كافية من MT5.</p>')+'</div>';
  if(pageHost){
   let html="";
   if(activeDashboardPage==="connection")html=accountPage;
   else if(activeDashboardPage==="open")html=openPage;
   else if(activeDashboardPage==="pending")html='<h4>الأوامر المعلقة فقط</h4>'+orderRows;
   else if(activeDashboardPage==="candles")html=candlePage;
   else if(activeDashboardPage==="indicator")html=indicatorPage;
   else if(activeDashboardPage==="control")html='<div class="dash-cards">'+[["حالة إذن التداول",d.trading_enabled?"مفعّل":"متوقف"],["حالة MT5",status],["آخر وصول",received],["عمر البيانات",d.age_ms==null?"—":d.age_ms+" ms"]].map(v=>'<article><small>'+esc(v[0])+'</small><b>'+esc(v[1])+'</b></article>').join("")+'</div>';
   pageHost.innerHTML=html;
  }
  if(settingsHost)settingsHost.innerHTML="";
 }catch(e){
  const pageHost=$("livePageData");if(pageHost)pageHost.innerHTML='<p class="muted">تعذر تحديث بيانات هذه الصفحة: '+esc(e.message)+'</p>';
 }finally{liveTelemetryBusy=false}
}

async function loadLicenseClients(){const host=$("licenseClientsList");if(!host)return;try{const d=await api("/api/admin/clients"),rows=d.clients||[];host.innerHTML=rows.length?rows.map(c=>'<div class="catalog-row"><b>'+esc(c.name||c.email)+' • '+(c.active?"نشط":"موقوف")+'</b><small>'+esc(c.email)+'</small><button class="dash-btn" data-id="'+esc(c.id)+'" data-active="'+c.active+'">'+(c.active?"إيقاف":"تفعيل")+'</button></div>').join(""):"<p class=muted>لا يوجد عملاء.</p>";host.querySelectorAll("button[data-id]").forEach(b=>b.onclick=async()=>{const active=b.dataset.active!=="true";if(!confirm(active?"تفعيل العميل؟":"إيقاف دخول العميل؟"))return;try{await api("/api/admin/clients/"+b.dataset.id,"PATCH",{active});await loadLicenseClients()}catch(e){alert(e.message)}})}catch(e){host.innerHTML='<p class="error">'+esc(e.message)+'</p>'}}
async function loadDashboardWorkspace(){
 const panel=document.querySelector(".dash-main .dash-panel");
 if(!panel)return;
 const id=activeDashboardPage;
 if(id==="licenses"){await loadLicenseClients();return;}if(!["home","connection","aiCouncil","performance","risk","gold","analysis","council","lab","results","learning","daily","logs","signals","blackbox","indicator","candles","trades","history","open","pending","settings"].includes(id))return;
 try{
  const d=await api("/api/engine/workspace"),a=d.analysis||{},x=a.context||{},z=a.adaptive||{},q=d.decision||{};
  if(id==="signals"){
   const s=await api("/api/engine/signals?limit=150"),events=s.events||[];
   const host=$("signalEventsList"),filter=$("signalFilter");
   const labels={SIGNAL_CREATED:"إشارة صادرة",WAIT:"انتظار",BLOCKED:"ممنوع — التداول متوقف",TRADE_OPENED:"تم فتح الصفقة من MT5",TRADE_CLOSED:"إغلاق مؤكد من MT5",ORDER_SENT:"تم إرسال الأمر",ORDER_REJECTED:"رفض الوسيط الأمر",ADDON_OPENED:"تعزيز مؤكد",COOLING_OPENED:"تبريد مؤكد",PROTECTION:"حماية"};
   const draw=()=>{const f=filter?.value||"ALL",rows=events.filter(v=>f==="ALL"||v.event_type===f);host.innerHTML=rows.length?rows.map(v=>{const q=v.payload||{},stamp=v.created_at?new Date(v.created_at).toLocaleString("ar-AE"):"—",decision=q.decision||q.side||"",reason=q.reason_ar||q.reason||q.message||"لا يوجد سبب مرفق";return '<div class="catalog-row"><b>'+esc(labels[v.event_type]||v.event_type)+' • '+esc(decision)+' • '+esc(q.symbol||"XAUUSD")+'</b><small>'+esc(stamp)+' • '+esc(q.strategy_name||("استراتيجية #"+(q.strategy_id||"—")))+' • الثقة: '+(q.confidence==null?"—":(Number(q.confidence)*100).toFixed(1)+"%")+' • '+esc(reason)+'</small><small>التذكرة: '+esc(q.deal_id||q.ticket||"—")+' • اللوت: '+esc(q.volume||"—")+' • السعر: '+esc(q.price||"—")+' • الربح الصافي: '+esc(q.net_profit??"—")+'</small></div>'}).join(""):'<p class="muted">لا توجد أحداث مطابقة حتى الآن. ستظهر إشارات WAIT/BUY/SELL من المحرك، وتأكيدات الصفقات عند وصولها من MT5.</p>'};
   if(host)draw();if(filter)filter.onchange=draw;const btn=$("refreshSignals");if(btn)btn.onclick=()=>loadDashboardWorkspace();return;
  }
  const escv=v=>esc(v==null?"—":v),num=v=>Number.isFinite(Number(v))?Number(v).toFixed(2):"—";
  const liveTerminal=d.terminal_state&&d.terminal_state.payload?d.terminal_state.payload:{},liveAccount=liveTerminal.account||{},livePositions=liveTerminal.positions||[];
  const liveFloatingProfit=livePositions.reduce((sum,p)=>sum+Number(p.profit||0)+Number(p.swap||0),0);
  const tile=(k,v)=>'<article><small>'+escv(k)+'</small><b>'+escv(v)+'</b></article>';
  const grid=arr=>'<div class="dash-cards">'+arr.join("")+'</div>';
  if(id==="settings"){
   const terminal=d.terminal_state&&d.terminal_state.payload?d.terminal_state.payload:{},liveSettings=terminal.settings||{},account=terminal.account||{},positions=terminal.positions||[],floatingProfit=positions.reduce((sum,p)=>sum+Number(p.profit||0)+Number(p.swap||0),0),yes=v=>v===true?"مفعّل":v===false?"متوقف":"—",stamp=terminal.updated_at?new Date(terminal.updated_at).toLocaleString("ar-AE"):"لم تصل بعد";
   const live=$("engineSettingsLive");
   if(live)live.innerHTML=grid([tile("حالة MT5",d.connected?"متصل":"غير متصل"),tile("السيرفر",yes(liveSettings.server_online)),tile("إذن التداول",yes(liveSettings.trading_enabled)),tile("رقم الحساب",account.login),tile("السيرفر الوسيط",account.server),tile("الرصيد",account.balance),tile("حقوق الحساب",account.equity),tile("الربح العائم",floatingProfit.toFixed(2)+" "+(account.currency||"")),tile("آخر تحديث",stamp)]);
   return;
  }
  let body="";
  if(id==="home"||id==="gold")body=grid([tile("حالة السوق",x.regime||"بانتظار البيانات"),tile("القرار",q.decision),tile("الاستراتيجية",q.strategy_name),tile("الثقة",q.confidence==null?"—":(Number(q.confidence)*100).toFixed(1)+"%"),tile("ATR",num(x.atr)),tile("قوة الاتجاه",num(x.trend_strength)),tile("جودة الشراء",num(x.buy_quality)),tile("جودة البيع",num(x.sell_quality))]);
  if(id==="indicator")body="<p class=\"muted\">المؤشر الصناعي اللحظي يعرض قراءته المباشرة في اللوحة أعلاه، ويُستخدم كمساعد تحليلي للاستراتيجيات والتركيبات فقط؛ لا يغيّر شروط الدخول ولا يرسل أوامر تداول.</p>";
  if(id==="analysis")body=grid([tile("نظام السوق",x.regime),tile("ATR Ratio",num(x.atr_ratio)),tile("ADX",num(x.adx)),tile("VWAP",num(x.vwap)),tile("الدعم",num(x.support)),tile("المقاومة",num(x.resistance)),tile("إعداد RSI",z.rsi_period),tile("التكيف",z.mode)])+'<p>'+escv(x.reason)+'</p>';
  if(id==="council"||id==="results")body=grid([tile("القرار المركزي",q.decision),tile("الاستراتيجية",q.strategy_name),tile("رقمها",q.strategy_id),tile("الثقة",q.confidence==null?"—":(Number(q.confidence)*100).toFixed(1)+"%")])+'<p>'+escv(q.reason_ar)+'</p>';
  if(id==="lab")body=grid([tile("جودة الشراء",num(x.buy_quality)),tile("جودة البيع",num(x.sell_quality)),tile("فخ شراء",x.buy_trap?"محتمل":"غير مرصود"),tile("فخ بيع",x.sell_trap?"محتمل":"غير مرصود")])+'<p>تحليل الشمعة الأخيرة؛ ليس اختباراً تاريخياً ولا ضماناً للربح.</p>';
  if(id==="gold"||id==="council"){
   const activeVotes=(a.votes||[]).filter(v=>v.signal!=="WAIT");
   const activeCombos=(a.combination_votes||[]).filter(v=>v.signal!=="WAIT");
   const alignment=v=>v.indicator_alignment||"غير متاح";
   body+='<h4>توافق المؤشر الصناعي مع الاستراتيجيات</h4><div class="catalog-list">'+(activeVotes.length?activeVotes.slice(0,30).map(v=>'<div class="catalog-row"><b>#'+escv(v.id)+' '+escv(v.name)+' • '+escv(v.signal)+'</b><small>توافق المؤشر: '+escv(alignment(v))+' • '+escv(v.reason)+'</small></div>').join(""):'<p class="muted">لا توجد استراتيجية ذات إشارة اتجاهية في آخر تحليل محفوظ.</p>')+'</div><h4>توافق المؤشر مع التركيبات</h4><div class="catalog-list">'+(activeCombos.length?activeCombos.slice(0,30).map(v=>'<div class="catalog-row"><b>#'+escv(v.id)+' '+escv(v.name)+' • '+escv(v.signal)+'</b><small>توافق المؤشر: '+escv(alignment(v))+' • '+escv(v.reason)+'</small></div>').join(""):'<p class="muted">لا توجد تركيبة ذات إشارة اتجاهية في آخر تحليل محفوظ.</p>')+'</div>';
  }

  if(id==="connection"){const live=d.live_state||{},tick=live.tick||{},candle=live.forming_candle||{};body=grid([tile("اتصال MT5",d.connected?"متصل":"غير متصل"),tile("عمر آخر بيانات",d.live_age_ms==null?"—":d.live_age_ms+" ms"),tile("زمن استجابة آخر طلب",lastApiRoundTripMs==null?"—":lastApiRoundTripMs+" ms"),tile("تسلسل MT5",d.live_sequence??"—"),tile("Bid / Ask",num(tick.bid)+" / "+num(tick.ask)),tile("آخر Tick",tick.time_msc?new Date(Number(tick.time_msc)).toLocaleTimeString("ar-AE"):"—"),tile("الشمعة الحالية",candle.time?"مستلمة":"غير متاحة"),tile("آخر وصول",d.last_seen_at?new Date(d.last_seen_at).toLocaleString("ar-AE"):"—")])+"<p class=muted>زمن الاستجابة يقيس طلب المتصفح إلى الخادم والعودة. عمر البيانات يقيس حداثة آخر لقطة MT5.</p>";}
  if(id==="aiCouncil"){const votes=a.votes||[],combos=a.combination_votes||[];body=grid([tile("القرار المركزي",q.decision),tile("الاستراتيجية المختارة",q.strategy_name),tile("الثقة",q.confidence==null?"—":(Number(q.confidence)*100).toFixed(1)+"%"),tile("تصويتات الاستراتيجيات",votes.length),tile("تصويتات التركيبات",combos.length)])+"<h4>تصويت الاستراتيجيات</h4><div class=catalog-list>"+(votes.length?votes.map(v=>'<div class=catalog-row><b>'+escv(v.name)+' • '+escv(v.signal)+'</b><small>'+escv(v.reason)+' • توافق المؤشر: '+escv(v.indicator_alignment||"غير متاح")+'</small></div>').join(""):"<p class=muted>لا توجد تصويتات في آخر تحليل.</p>")+"</div><h4>تصويت التركيبات</h4><div class=catalog-list>"+(combos.length?combos.map(v=>'<div class=catalog-row><b>'+escv(v.name)+' • '+escv(v.signal)+'</b><small>'+escv(v.reason)+' • توافق المؤشر: '+escv(v.indicator_alignment||"غير متاح")+'</small></div>').join(""):"<p class=muted>لا توجد تركيبات مصوتة في آخر تحليل.</p>")+"</div>";}
  if(id==="performance"){const by=new Map();(d.outcomes||[]).forEach(v=>{const key=String(v.strategy_id??"غير معروف"),r=by.get(key)||{id:key,count:0,wins:0,losses:0,pnl:0},p=Number(v.pnl||0);r.count++;r.pnl+=p;if(p>0)r.wins++;else if(p<0)r.losses++;by.set(key,r)});const rows=[...by.values()].sort((a,b)=>b.pnl-a.pnl);body=grid([tile("نتائج مسجلة",d.outcome_count??0),tile("استراتيجيات لها نتائج",rows.length),tile("صافي النتائج المسجلة",rows.reduce((n,v)=>n+v.pnl,0).toFixed(2)),tile("أعلى صافي مسجل",rows[0]?"#"+rows[0].id:"—")])+"<p class=muted>الإحصاءات مبنية فقط على نتائج الصفقات التي وصلت وسُجلت للمحرك. لا تمثل اختباراً تاريخياً كاملاً.</p><div class=catalog-list>"+(rows.length?rows.map(v=>'<div class=catalog-row><b>استراتيجية #'+escv(v.id)+' • صافي '+v.pnl.toFixed(2)+'</b><small>نتائج '+v.count+' • رابحة '+v.wins+' • خاسرة '+v.losses+' • نسبة الفوز '+(v.count?(v.wins/v.count*100).toFixed(1):"0")+'%</small></div>').join(""):"<p class=muted>لا توجد نتائج فعلية مسجلة كافية بعد.</p>")+"</div>";}
  if(id==="risk"){const positions=livePositions,volume=positions.reduce((n,p)=>n+Number(p.volume||0),0),losses=positions.filter(p=>Number(p.profit||0)+Number(p.swap||0)<0).length;body=grid([tile("إجمالي حجم التعرض",num(volume)+" لوت"),tile("عدد الصفقات الخاسرة حالياً",losses),tile("عدد الصفقات الرابحة حالياً",positions.filter(p=>Number(p.profit||0)+Number(p.swap||0)>0).length)])+"<p class=muted>مراقبة فقط: لا يتم إغلاق الصفقات أو تعديل SL/TP. ملخص الرصيد والصفقات التفصيلي موجود في صفحة الحساب والصفقات المفتوحة.</p>";}
  if(id==="learning")body=grid([tile("نتائج تعلم محفوظة",d.outcome_count),tile("أوزان الاستراتيجيات",Array.isArray(d.weights)?d.weights.length:0)])+'<div class="catalog-list">'+(d.outcomes||[]).map(v=>'<div class="catalog-row"><b>Strategy #'+escv(v.strategy_id)+'</b><small>Deal '+escv(v.deal_id)+' • P/L '+num(v.pnl)+'</small></div>').join("")+'</div>';
  const terminal=d.terminal_state&&d.terminal_state.payload?d.terminal_state.payload:{};
  const positions=terminal.positions||[],orders=terminal.orders||[],history=terminal.history||[],settings=terminal.settings||{},candles=d.candles||[];
  if(id==="daily"){
   const closed=history.filter(v=>[1,2,3].includes(Number(v.entry))).sort((a,b)=>Number(b.time||0)-Number(a.time||0));
   const byDay=new Map();
   closed.forEach(v=>{
    const t=Number(v.time||0);if(!t)return;
    const dt=new Date(t*1000),day=dt.getFullYear()+"-"+String(dt.getMonth()+1).padStart(2,"0")+"-"+String(dt.getDate()).padStart(2,"0");
    const pnl=Number(v.net_profit??(Number(v.profit||0)+Number(v.commission||0)+Number(v.swap||0)+Number(v.fee||0)));
    const row=byDay.get(day)||{day,pnl:0,count:0,wins:0,losses:0};
    row.pnl+=pnl;row.count++;if(pnl>0)row.wins++;else if(pnl<0)row.losses++;
    byDay.set(day,row);
   });
   const days=[...byDay.values()].sort((a,b)=>b.day.localeCompare(a.day));
   const total=days.reduce((s,v)=>s+v.pnl,0),count=days.reduce((s,v)=>s+v.count,0),wins=days.reduce((s,v)=>s+v.wins,0),losses=days.reduce((s,v)=>s+v.losses,0);
   const money=v=>Number(v||0).toFixed(2)+" "+escv(terminal.account?.currency||"");
   const table=days.length?'<div class="catalog-list">'+days.map(v=>'<div class="catalog-row"><b>'+escv(v.day)+' • صافي '+money(v.pnl)+'</b><small>صفقات مغلقة: '+v.count+' • رابحة: '+v.wins+' • خاسرة: '+v.losses+'</small></div>').join("")+'</div>':'<p class="muted">لا توجد صفقات مغلقة مستلمة من MT5 خلال آخر 30 يوماً. تأكد من تشغيل AL-ZOOL Central Client وانتظر تحديث سجل الصفقات.</p>';
   body=grid([tile("صافي الربح / الخسارة",money(total)),tile("الصفقات المغلقة",count),tile("أيام بها تنفيذ",days.length),tile("رابحة / خاسرة",wins+" / "+losses),tile("آخر تحديث MT5",terminal.updated_at?new Date(terminal.updated_at).toLocaleString("ar-AE"):"لم تصل بيانات MT5")])+'<h4>الأداء اليومي الفعلي — آخر 30 يوماً</h4><p class="muted">الحساب من صفقات MT5 المغلقة للإكسبرت والرمز الحالي، شاملاً الربح والعمولة والسواب والرسوم. لا تُستخدم نتائج التعلّم بدلاً من سجل الحساب.</p>'+table;
  }
  const rows=(arr,cols)=>arr.length?`<div class="catalog-list">${arr.map(v=>`<div class="catalog-row"><b>${cols.map(k=>escv(v[k])).join(" • ")}</b><small>${escv(v.symbol||"")} ${v.ticket?"• #"+escv(v.ticket):""}</small></div>`).join("")}</div>`:"<p class=\"muted\">لا توجد سجلات مستلمة من MT5 بعد.</p>";
  if(id==="open")body='<h4>الصفقات المفتوحة — بيانات مباشرة من MT5</h4>';
  if(id==="pending")body="<h4>آخر الصفقات المنفذة (30 يوم)</h4>"+rows(history.slice().reverse().slice(0,100),["entry","type","volume","price","profit","commission","swap"]);
  if(id==="trades")body="<h4>سجل التنفيذات المستلمة من MT5</h4>"+rows(history.slice().reverse().slice(0,100),["entry","type","volume","price","profit"]);
  if(id==="history"){
   const sorted=history.slice().sort((a,b)=>Number(b.time||0)-Number(a.time||0));
   const net=sorted.reduce((sum,v)=>sum+Number(v.net_profit??(Number(v.profit||0)+Number(v.commission||0)+Number(v.swap||0)+Number(v.fee||0))),0);
   const deals=sorted.map(v=>{
    const type=Number(v.type)===0?"شراء":Number(v.type)===1?"بيع":"نوع "+escv(v.type);
    const entry=({0:"دخول",1:"خروج",2:"عكس",3:"إغلاق مقابل"})[Number(v.entry)]||"—";
    const stamp=Number(v.time)>0?new Date(Number(v.time)*1000).toLocaleString("ar-AE"):"—";
    const pnl=Number(v.net_profit??(Number(v.profit||0)+Number(v.commission||0)+Number(v.swap||0)+Number(v.fee||0)));
    return '<div class="catalog-row"><b>'+type+' • '+entry+' • '+escv(v.symbol)+' • '+escv(v.volume)+' لوت</b><small>وقت التنفيذ: '+escv(stamp)+' • السعر: '+escv(v.price)+' • صافي النتيجة: '+pnl.toFixed(2)+' • ربح: '+num(v.profit)+' • عمولة: '+num(v.commission)+' • Swap: '+num(v.swap)+' • Fee: '+num(v.fee)+' • تذكرة: #'+escv(v.ticket)+'</small></div>';
   }).join("");
   body=grid([tile("عدد التنفيذات",sorted.length),tile("صافي النتائج المعروضة",net.toFixed(2)),tile("الفترة","آخر 30 يوماً / حتى 200 تنفيذ"),tile("آخر تحديث",terminal.updated_at?new Date(terminal.updated_at).toLocaleString("ar-AE"):"لم يصل بعد")])+'<h4>سجل التنفيذات — '+escv(settings.chart_symbol||"XAUUSD")+' / Magic '+escv(settings.magic||"")+'</h4><div class="catalog-list">'+(deals||'<p class="muted">لا توجد صفقات لهذا الإكسبرت في آخر 30 يوماً. افتح MT5 وانتظر إرسال سجل الصفقات.</p>')+'</div>';
  }
  if(id==="settings"){const account=terminal.account||{},yes=v=>v===true?"مفعّل":v===false?"متوقف":"—",stamp=terminal.updated_at?new Date(terminal.updated_at).toLocaleString("ar-AE"):"لم تصل بعد";const live=$("engineSettingsLive");if(live)live.innerHTML=grid([tile("حالة MT5",d.connected?"متصل":"غير متصل"),tile("السيرفر",yes(settings.server_online)),tile("إذن التداول",yes(settings.trading_enabled)),tile("الحساب",account.login),tile("السيرفر الوسيط",account.server),tile("الرصيد",account.balance),tile("حقوق الحساب",account.equity),tile("الربح العائم",account.profit),tile("آخر تحديث",stamp)]);body="<h4>حالة المحرك المركزي وMT5</h4>"+grid([tile("حالة MT5",d.connected?"متصل":"غير متصل / بيانات السوق قديمة"),tile("حالة اتصال السيرفر",yes(settings.server_online)),tile("إذن التداول من لوحة التحكم",yes(settings.trading_enabled)),tile("حالة الإكسبرت",settings.engine_status),tile("آخر قرار مركزي",settings.last_decision),tile("الاستراتيجية الحالية",settings.last_strategy),tile("رقم الاستراتيجية",settings.last_strategy_id),tile("الثقة",settings.confidence==null?"—":(Number(settings.confidence)*100).toFixed(1)+"%"),tile("آخر تحديث من MT5",stamp)])+"<h4>بيانات الحساب الفعلية</h4>"+grid([tile("رقم الحساب",account.login),tile("شركة الوساطة / السيرفر",account.server),tile("عملة الحساب",account.currency),tile("الرصيد",account.balance),tile("حقوق الحساب",account.equity),tile("الهامش المستخدم",account.margin),tile("الهامش المتاح",account.free_margin),tile("الربح العائم",account.profit)])+"<h4>إعدادات التنفيذ الحالية من MT5</h4>"+grid([tile("رابط المحرك",settings.server_url),tile("Engine ID",settings.engine_id),tile("رمز الشارت",settings.chart_symbol),tile("الفريم",settings.timeframe),tile("حجم اللوت",settings.lots),tile("Magic Number",settings.magic),tile("الحد الأقصى للصفقات",settings.max_positions),tile("أقصى سبريد (نقطة)",settings.max_spread_points),tile("السماح بالشراء",yes(settings.allow_buy)),tile("السماح بالبيع",yes(settings.allow_sell)),tile("منع الصفقات العكسية",yes(settings.block_opposite)),tile("اشتراط وقف الوسيط",yes(settings.require_broker_stops)),tile("ATR Period",settings.atr_period),tile("وقف الخسارة ATR",settings.sl_atr),tile("هدف الربح ATR",settings.tp_atr),tile("Trailing",yes(settings.trailing)),tile("بدء التريلينغ ATR",settings.trail_start_atr),tile("مسافة التريلينغ ATR",settings.trail_distance_atr),tile("فترة طلب القرار (ثانية)",settings.request_seconds),tile("عدد الشموع المرسلة",settings.bars_to_send)])+"<p class=\"muted\">المعروض قراءة فعلية لما يرسله الإكسبرت المتصل. لا يظهر رمز Engine Token ولا يمكن تعديل إعدادات المخاطرة عن بُعد من هذه الصفحة.</p>";if(!terminal.updated_at)body="<p class=\"muted\">لم تصل بيانات الإعدادات من MT5 حتى الآن. افتح MT5 وشغّل الإكسبرت AL-ZOOL Central Client على شارت الذهب، وتأكد من السماح بعنوان السيرفر في WebRequest ثم انتظر تحديثاً واحداً.</p>"+body;}
  if(id==="candles"){const vals=candles.slice(-60);if(vals.length){const lo=Math.min(...vals.map(v=>Number(v.low))),hi=Math.max(...vals.map(v=>Number(v.high))),span=Math.max(hi-lo,1e-9);const bars=vals.map((v,i)=>{const x=10+i*9,w=5,y1=10+(hi-Number(v.high))/span*180,y2=10+(hi-Number(v.low))/span*180,yo=10+(hi-Number(v.open))/span*180,yc=10+(hi-Number(v.close))/span*180;const up=Number(v.close)>=Number(v.open);return `<g stroke="${up?"#16a34a":"#dc2626"}" fill="${up?"#16a34a":"#dc2626"}"><path d="M${x+2.5} ${y1}V${y2}"/><rect x="${x}" y="${Math.min(yo,yc)}" width="${w}" height="${Math.max(1,Math.abs(yc-yo))}"/></g>`}).join("");body=`<p>آخر ${vals.length} شمعة مغلقة • ${escv(settings.timeframe||"الفريم الحالي")}</p><svg viewBox="0 0 ${Math.max(550,vals.length*9+20)} 205" width="100%" role="img">${bars}</svg><p>آخر إغلاق: ${escv(vals[vals.length-1].close)} • أعلى: ${escv(hi)} • أدنى: ${escv(lo)}</p>`}else body="<p class=\"muted\">بانتظار وصول بيانات الشموع من MT5.</p>"}
  if(id==="logs"||id==="blackbox")body=grid([tile("Decision ID",q.decision_id),tile("القرار",q.decision),tile("الاستراتيجية",q.strategy_name),tile("آخر وصول",d.last_seen_at)])+'<pre>'+esc(JSON.stringify({reason:q.reason_ar,context:x,adaptive:z},null,2))+'</pre>';
  if(!d.connected && id!=="settings")body='<p class="muted">لا توجد بيانات سوق حديثة من MT5. '+escv(d.reason)+'</p>'+body;
  panel.innerHTML='<h3>بيانات المحرك الفعلية</h3>'+body;
  refreshLiveTelemetry();
 }catch(e){panel.innerHTML='<h3>بيانات المحرك</h3><p class="error">'+esc(e.message)+'</p>'}
}
