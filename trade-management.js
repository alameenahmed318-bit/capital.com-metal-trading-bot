let supabaseClient=null,accessToken=null,refreshBusy=false;
const $=id=>document.getElementById(id);
const fmt=(v,d=2)=>v==null||!Number.isFinite(Number(v))?"—":new Intl.NumberFormat("en-US",{minimumFractionDigits:0,maximumFractionDigits:d}).format(Number(v));
const esc=v=>String(v??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
function api(path,options={}){const headers={...(accessToken?{Authorization:"Bearer "+accessToken}:{}) ,...(options.headers||{})};return fetch(path,{...options,headers}).then(async r=>{const d=await r.json().catch(()=>({}));if(!r.ok)throw new Error(d.error||"API "+r.status);return d;});}
async function loadCloseSettings(){
 const d=await api("/api/trade-management/settings"),s=d.settings||{};
 $("singleEnabled").checked=!!s.single_enabled;
 $("groupEnabled").checked=!!s.group_enabled;
 $("singleTarget").value=Number(s.single_profit_target??1).toFixed(2);
 $("groupTarget").value=Number(s.group_profit_target??5).toFixed(2);
 $("stopLossEnabled").checked=s.stop_loss_enabled!==false;
 $("stopLossMode").value=(s.stop_loss_mode==="POINTS"?"POINTS":"ATR");
 $("stopLossValue").value=Number(s.stop_loss_value??1.8).toString();
 $("closeEngineState").textContent="الإعدادات محفوظة · "+(d.client_id?"مرتبطة بعميل MT5":"غير مرتبطة");
 $("closeSettingsMessage").textContent="الأهداف بعملة حساب MT5: "+(s.currency||"عملة الحساب");
 $("protectionSettingsMessage").textContent="إعدادات وقف الخسارة الحالية محفوظة.";
 updateStopLossHelp();
 const pm=s.position_management||{};
 $("entryGuardEnabled").checked=pm.entry_guard_enabled!==false;
 $("entryMaxDelayBars").value=Number(pm.entry_max_delay_bars??1);
 $("entryMaxDeviationAtr").value=Number(pm.entry_max_deviation_atr??0.5);
 $("recoveryEnabled").checked=!!pm.recovery_enabled;
 $("recoveryThreshold").value=pm.recovery_loss_threshold>0?String(pm.recovery_loss_threshold):"";
 $("recoveryMessage").textContent=pm.recovery_enabled?"التعافي مفعّل على MT5 وينتظر حد الخسارة.":"التعافي متوقف. لن يفتح صفقات قبل تفعيله.";
 $("pmMode").value=pm.mode==="MANUAL"?"MANUAL":"AI";
 $("addonEnabled").checked=!!pm.addon_enabled;
 $("addonMax").value=Number(pm.addon_max??0);
 $("addonMultiplier").value=Number(pm.addon_multiplier??1.5);
 $("addonMaxLot").value=Number(pm.addon_max_lot??0.05);
 $("addonLot").value=Number(pm.addon_lot??0.01);
 $("addonDistance").value=Number(pm.addon_distance_points??100);
 $("coolingEnabled").checked=!!pm.cooling_enabled;
 $("coolingMax").value=Number(pm.cooling_max??0);
 $("coolingMultiplier").value=Number(pm.cooling_multiplier??1.2);
 $("coolingMaxLot").value=Number(pm.cooling_max_lot??0.05);
 $("coolingLot").value=Number(pm.cooling_lot??0.01);
 $("coolingDistance").value=Number(pm.cooling_distance_points??100);
 $("maxTotalLots").value=Number(pm.max_total_lots??0.10);
 updatePmModeUI();
}
function updateStopLossHelp(){
 const mode=$("stopLossMode").value;
 $("stopLossHelp").textContent=mode==="ATR"
  ?"حسب ATR: القيمة مضاعف لمؤشر ATR. عند إيقاف الخيار لن يُرسل وقف خسارة أولي للصفقات الجديدة. التريلينغ الحالي لا يتغير."
  :"بالنقاط: القيمة عدد نقاط الرمز (_Point). عند إيقاف الخيار لن يُرسل وقف خسارة أولي للصفقات الجديدة. التريلينغ الحالي لا يتغير.";
}
async function saveProtectionSettings(){
 const value=Number($("stopLossValue").value),mode=$("stopLossMode").value;
 if(!Number.isFinite(value)||value<=0||value>1000000){$("protectionSettingsMessage").textContent="أدخل قيمة وقف خسارة أكبر من صفر.";return;}
 if(mode!=="ATR"&&mode!=="POINTS"){ $("protectionSettingsMessage").textContent="اختر نوع وقف خسارة صحيح.";return;}
 $("saveProtectionSettings").disabled=true;
 $("protectionSettingsMessage").textContent="جارٍ حفظ إعدادات وقف الخسارة…";
 try{
  await api("/api/trade-management/settings",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({
   single_enabled:$("singleEnabled").checked,single_profit_target:Number($("singleTarget").value),
   group_enabled:$("groupEnabled").checked,group_profit_target:Number($("groupTarget").value),
   stop_loss_enabled:$("stopLossEnabled").checked,stop_loss_mode:mode,stop_loss_value:value
  })});
  $("protectionSettingsMessage").textContent="تم الحفظ. سيستلم MT5 الإعدادات خلال دورة التحديث التالية.";
  $("closeEngineState").textContent="الإعدادات محفوظة على الخادم";
 }catch(e){$("protectionSettingsMessage").textContent="تعذر الحفظ: "+e.message;}
 finally{$("saveProtectionSettings").disabled=false;}
}
async function saveCloseSettings(){
 const single=Number($("singleTarget").value),group=Number($("groupTarget").value);
 if(!Number.isFinite(single)||single<=0||!Number.isFinite(group)||group<=0){$("closeSettingsMessage").textContent="أدخل هدفَي ربح موجبين.";return;}
 $("saveCloseSettings").disabled=true;$("closeSettingsMessage").textContent="جارٍ حفظ الإعدادات…";
 try{
  const d=await api("/api/trade-management/settings",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({
   single_enabled:$("singleEnabled").checked,single_profit_target:single,
   group_enabled:$("groupEnabled").checked,group_profit_target:group
  })});
  $("closeSettingsMessage").textContent="تم حفظ الإعدادات بنجاح. ستصل إلى MT5 عند اتصال عميل MT5 المحدّث.";
  $("closeEngineState").textContent="الإعدادات محفوظة على الخادم";
 }catch(e){$("closeSettingsMessage").textContent="تعذر الحفظ: "+e.message;}
 finally{$("saveCloseSettings").disabled=false;}
}
function updatePmModeUI(){
 const ai=$("pmMode").value==="AI";
 document.querySelectorAll(".pm-manual-fields").forEach(el=>el.style.display=ai?"none":"grid");
 $("pmModeHelp").textContent=ai
  ?"AI تلقائي: المحرك يختار توقيت الإضافة واللوت والمسافة، ويلتزم بالحد الأقصى الذي تحدده لكل قسم."
  :"يدوي: تحدد اللوت والمسافة بالنقاط والحد الأقصى لكل قسم، والتنفيذ تلقائي عند تحقق المسافة ضد الصفقة الخاسرة.";
}
async function savePositionManagement(){
 const mode=$("pmMode").value;
 const cfg={mode,
  entry_guard_enabled:$("entryGuardEnabled").checked,
  entry_max_delay_bars:Number($("entryMaxDelayBars").value),
  entry_max_deviation_atr:Number($("entryMaxDeviationAtr").value),
  addon_enabled:$("addonEnabled").checked,addon_max:Number($("addonMax").value),
  addon_multiplier:Number($("addonMultiplier").value),addon_max_lot:Number($("addonMaxLot").value),
  addon_lot:Number($("addonLot").value),addon_distance_points:Number($("addonDistance").value),
  cooling_enabled:$("coolingEnabled").checked,cooling_max:Number($("coolingMax").value),
  cooling_multiplier:Number($("coolingMultiplier").value),cooling_max_lot:Number($("coolingMaxLot").value),
  cooling_lot:Number($("coolingLot").value),cooling_distance_points:Number($("coolingDistance").value),
  max_total_lots:Number($("maxTotalLots").value)};
 if(!Number.isInteger(cfg.entry_max_delay_bars)||cfg.entry_max_delay_bars<1||cfg.entry_max_delay_bars>10){$("pmMessage").textContent="أقصى تأخير يجب أن يكون من 1 إلى 10 شموع.";return;}
 if(!Number.isFinite(cfg.entry_max_deviation_atr)||cfg.entry_max_deviation_atr<0.05||cfg.entry_max_deviation_atr>20){$("pmMessage").textContent="مضاعف ATR يجب أن يكون بين 0.05 و20.";return;}
 for(const key of ["addon_max","cooling_max"]){
  if(!Number.isInteger(cfg[key])||cfg[key]<0||cfg[key]>100){$("pmMessage").textContent="الحد الأقصى يجب أن يكون عدداً صحيحاً من 0 إلى 100.";return;}
 }
 for(const key of ["addon_lot","cooling_lot","addon_max_lot","cooling_max_lot"]){
  if(!Number.isFinite(cfg[key])||cfg[key]<=0||cfg[key]>100){$("pmMessage").textContent="تحقق من قيم اللوت والحد الأقصى لكل صفقة.";return;}
 }
 for(const key of ["addon_multiplier","cooling_multiplier"]){
  if(!Number.isFinite(cfg[key])||cfg[key]<1||cfg[key]>3){$("pmMessage").textContent="المضاعف يجب أن يكون بين 1.0 و3.0.";return;}
 }
 if(!Number.isFinite(cfg.max_total_lots)||cfg.max_total_lots<=0||cfg.max_total_lots>100){
  $("pmMessage").textContent="أدخل سقفاً موجباً لإجمالي اللوتات.";return;
 }
 if(mode==="MANUAL"){
  for(const key of ["addon_distance_points","cooling_distance_points"]){
   if(!Number.isFinite(cfg[key])||cfg[key]<=0||cfg[key]>100000000){$("pmMessage").textContent="في الوضع اليدوي أدخل مسافة موجبة بالنقاط.";return;}
  }
 }
 if((cfg.addon_enabled&&cfg.addon_max<1)||(cfg.cooling_enabled&&cfg.cooling_max<1)){
  $("pmMessage").textContent="حدد الحد الأقصى لكل قسم قبل تفعيله.";return;
 }
 $("savePositionManagement").disabled=true;$("pmMessage").textContent="جارٍ حفظ إعدادات التعزيز والتبريد…";
 try{
  await api("/api/trade-management/settings",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({
   single_enabled:$("singleEnabled").checked,single_profit_target:Number($("singleTarget").value),
   group_enabled:$("groupEnabled").checked,group_profit_target:Number($("groupTarget").value),
   position_management:cfg
  })});
  $("pmMessage").textContent="تم الحفظ. سيستلم MT5 الإعدادات خلال دورة التحديث التالية.";
 }catch(e){$("pmMessage").textContent="تعذر الحفظ: "+e.message;}
 finally{$("savePositionManagement").disabled=false;}
}
async function saveRecoverySettings(){
 const threshold=Number($("recoveryThreshold").value);
 const enabled=$("recoveryEnabled").checked;
 if(enabled&&(!Number.isFinite(threshold)||threshold<=0)){$("recoveryMessage").textContent="أدخل حد خسارة موجبًا قبل تشغيل التعافي.";return;}
 const safeThreshold=Number.isFinite(threshold)&&threshold>0?threshold:0;
 $("saveRecoverySettings").disabled=true;
 $("recoveryMessage").textContent="جارٍ حفظ إعدادات التعافي…";
 try{
  const current=await api("/api/trade-management/settings");
  const s=current.settings||{};
  const pm=s.position_management||{};
  pm.recovery_enabled=enabled;
  pm.recovery_loss_threshold=safeThreshold;
  await api("/api/trade-management/settings",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({
   single_enabled:!!s.single_enabled,single_profit_target:Number(s.single_profit_target??1),
   group_enabled:!!s.group_enabled,group_profit_target:Number(s.group_profit_target??5),
   position_management:pm
  })});
  $("recoveryMessage").textContent=enabled
   ?"تم الحفظ والتفعيل. سيستلم MT5 الإعدادات خلال دورة التحديث التالية."
   :"تم الحفظ. التعافي متوقف ولن يفتح صفقات جديدة.";
 }catch(e){$("recoveryMessage").textContent="تعذر الحفظ: "+e.message;}
 finally{$("saveRecoverySettings").disabled=false;}
}
function updateRecoveryPreview(positions,currency){
 const rows=Array.isArray(positions)?positions:[];
 const floating=rows.reduce((sum,p)=>sum+Number(p.profit||0)+Number(p.swap||0),0);
 const lots=rows.reduce((sum,p)=>sum+Number(p.volume||0),0);
 $("recoveryFloating").textContent=money(floating,currency);
 $("recoveryLots").textContent=fmt(lots,3);
 $("recoveryLot").textContent=fmt(lots*2,3);
}
function pnlClass(v){const n=Number(v);return !Number.isFinite(n)||n===0?"pnl-neutral":n>0?"pnl-positive":"pnl-negative";}
function money(v,c){return v==null?"—":fmt(v)+" "+esc(c||"");}
function setPnl(el,v,c){el.textContent=money(v,c);el.classList.remove("pnl-positive","pnl-negative","pnl-neutral");el.classList.add(pnlClass(v));}
function direction(type){const n=Number(type);return n===0?'<span class="direction buy">شراء</span>':n===1?'<span class="direction sell">بيع</span>':'<span class="direction">—</span>';}
function orderType(type){return ({0:"شراء بسعر السوق",1:"بيع بسعر السوق",2:"Buy Limit",3:"Sell Limit",4:"Buy Stop",5:"Sell Stop",6:"Buy Stop Limit",7:"Sell Stop Limit"})[Number(type)]||"أمر معلّق";}
function timeText(value){if(!value)return "—";const d=new Date(value);return Number.isNaN(d.getTime())?"—":new Intl.DateTimeFormat("en-AE",{dateStyle:"short",timeStyle:"short"}).format(d);}
function emptyRow(cols,msg){return "<tr><td colspan='"+cols+"' class='empty-cell'>"+esc(msg)+"</td></tr>";}
function renderPositions(rows,currency){
 const body=$("positionsBody"),mobile=$("positionsMobile");
 $("positionsLabel").textContent=rows.length+" صفقة";
 if(!rows.length){body.innerHTML=emptyRow(9,"لا توجد صفقات مفتوحة في MT5");mobile.innerHTML='<div class="empty-state">لا توجد صفقات مفتوحة في MT5</div>';$("protectionBody").innerHTML=emptyRow(5,"لا توجد صفقات لمراجعة الحماية");$("slCoverage").textContent="لا توجد صفقات";$("tpCoverage").textContent="لا توجد صفقات";return;}
 body.innerHTML=rows.map(p=>{const net=Number(p.profit||0)+Number(p.swap||0);return "<tr><td>"+esc(p.ticket)+"</td><td>"+esc(p.symbol||"—")+"</td><td>"+direction(p.type)+"</td><td>"+fmt(p.volume,3)+"</td><td>"+fmt(p.open_price,3)+"</td><td>"+fmt(p.price_current,3)+"</td><td>"+fmt(p.sl,3)+"</td><td>"+fmt(p.tp,3)+"</td><td class='"+(net>0?"positive":net<0?"negative":"")+"'>"+money(net,currency)+"</td></tr>";}).join("");
 mobile.innerHTML=rows.map(p=>{const net=Number(p.profit||0)+Number(p.swap||0);return '<article class="mobile-card"><div class="mobile-card-head"><b>'+esc(p.symbol||"—")+' · #'+esc(p.ticket)+'</b>'+direction(p.type)+'</div><div class="mobile-fields"><div><small>اللوت</small><strong>'+fmt(p.volume,3)+'</strong></div><div><small>الربح العائم</small><strong class="'+(net>0?"positive":net<0?"negative":"")+'">'+money(net,currency)+'</strong></div><div><small>سعر الدخول</small><strong>'+fmt(p.open_price,3)+'</strong></div><div><small>السعر الحالي</small><strong>'+fmt(p.price_current,3)+'</strong></div><div><small>وقف الخسارة</small><strong>'+fmt(p.sl,3)+'</strong></div><div><small>جني الأرباح</small><strong>'+fmt(p.tp,3)+'</strong></div></div></article>';}).join("");
 const withSl=rows.filter(p=>Number(p.sl)>0).length,withTp=rows.filter(p=>Number(p.tp)>0).length;
 $("slCoverage").textContent=withSl+" / "+rows.length;$("slCoverage").className=withSl===rows.length?"state-good":"state-warn";
 $("tpCoverage").textContent=withTp+" / "+rows.length;$("tpCoverage").className=withTp===rows.length?"state-good":"state-warn";
 $("protectionBody").innerHTML=rows.map(p=>{const sl=Number(p.sl)>0,tp=Number(p.tp)>0;return "<tr><td>"+esc(p.ticket)+"</td><td>"+esc(p.symbol||"—")+"</td><td>"+fmt(p.sl,3)+"</td><td>"+fmt(p.tp,3)+"</td><td class='"+(sl&&tp?"state-good":"state-warn")+"'>"+(sl&&tp?"SL و TP مضبوطين":sl?"وقف خسارة فقط":tp?"جني أرباح فقط":"بدون SL / TP")+"</td></tr>";}).join("");
}
function renderOrders(rows,currency){
 $("ordersLabel").textContent=rows.length+" أمر";
 $("pendingCount").textContent=fmt(rows.length);
 if(!rows.length){$("ordersBody").innerHTML=emptyRow(8,"لا توجد أوامر معلقة في MT5");$("ordersMobile").innerHTML='<div class="empty-state">لا توجد أوامر معلقة في MT5</div>';return;}
 $("ordersBody").innerHTML=rows.map(o=>"<tr><td>"+esc(o.ticket)+"</td><td>"+esc(o.symbol||"—")+"</td><td>"+esc(orderType(o.type))+"</td><td>"+fmt(o.volume,3)+"</td><td>"+fmt(o.price,3)+"</td><td>"+fmt(o.sl,3)+"</td><td>"+fmt(o.tp,3)+"</td><td>"+esc(String(o.state??"—"))+"</td></tr>").join("");
 $("ordersMobile").innerHTML=rows.map(o=>'<article class="mobile-card"><div class="mobile-card-head"><b>'+esc(o.symbol||"—")+' · #'+esc(o.ticket)+'</b><span class="read-only">'+esc(orderType(o.type))+'</span></div><div class="mobile-fields"><div><small>الحجم</small><strong>'+fmt(o.volume,3)+'</strong></div><div><small>السعر</small><strong>'+fmt(o.price,3)+'</strong></div><div><small>وقف الخسارة</small><strong>'+fmt(o.sl,3)+'</strong></div><div><small>جني الأرباح</small><strong>'+fmt(o.tp,3)+'</strong></div></div></article>').join("");
}
async function refresh(){
 if(refreshBusy||!accessToken)return;refreshBusy=true;$("refreshBtn").disabled=true;
 $("clock").textContent=new Intl.DateTimeFormat("en-AE",{dateStyle:"medium",timeStyle:"short"}).format(new Date());
 try{
  const s=await api("/api/dashboard/summary"),currency=s.currency||s.account?.currency||"";
  setPnl($("floating"),s.floating_pl,currency);
  $("openCount").textContent=fmt(s.open_trades??(s.positions||[]).length,0);
  $("equity").textContent=money(s.equity,currency);
  $("balanceMeta").textContent="الرصيد: "+money(s.balance,currency);
  $("floatingMeta").textContent=s.connected?"يتغير تلقائيًا مع بيانات MT5":"بانتظار اتصال MT5";
  renderPositions(Array.isArray(s.positions)?s.positions:[],currency);
  updateRecoveryPreview(s.positions,currency);
  renderOrders(Array.isArray(s.orders)?s.orders:[],currency);
  $("lastSeen").textContent=timeText(s.last_seen);
  $("serviceState").textContent=s.connected?"بيانات MT5 مباشرة":"بانتظار اتصال MT5";
  if(s.connected){$("dataNotice").innerHTML="<b>MT5 متصل</b><span>آخر تحديث: "+esc(timeText(s.last_seen))+" · تتم قراءة المراكز والأوامر مباشرة دون إرسال أوامر تداول.</span>";}
  else{$("dataNotice").innerHTML="<b>MT5 غير متصل</b><span>لم تصل بيانات حديثة. افتح الطرفية وتأكد من تشغيل EA واتصال الترخيص.</span>";}
 }catch(e){$("serviceState").textContent="تعذر تحميل البيانات";$("dataNotice").innerHTML="<b>تعذر تحديث بيانات التداول</b><span>"+esc(e.message)+"</span>";$("floatingMeta").textContent=e.message;}
 finally{refreshBusy=false;$("refreshBtn").disabled=false;}
}

function normalizePmDigits(input){
 const map={"٠":"0","١":"1","٢":"2","٣":"3","٤":"4","٥":"5","٦":"6","٧":"7","٨":"8","٩":"9","۰":"0","۱":"1","۲":"2","۳":"3","۴":"4","۵":"5","۶":"6","۷":"7","۸":"8","۹":"9"};
 let value=String(input.value||"").replace(/[٠-٩۰-۹]/g,ch=>map[ch]);
 value=value.replace(/[^0-9.]/g,"");
 if(input.inputMode==="numeric") value=value.replace(/\./g,"");
 const dot=value.indexOf(".");
 if(dot>=0) value=value.slice(0,dot+1)+value.slice(dot+1).replace(/\./g,"");
 if(value!==input.value) input.value=value;
}
["recoveryThreshold","addonMax","addonMultiplier","addonMaxLot","addonLot","addonDistance","coolingMax","coolingMultiplier","coolingMaxLot","coolingLot","coolingDistance","maxTotalLots"].forEach(id=>{
 const field=$(id);
 field.setAttribute("lang","en-US");
 field.setAttribute("dir","ltr");
 field.addEventListener("input",()=>normalizePmDigits(field));
 field.addEventListener("change",()=>normalizePmDigits(field));
});

async function boot(){
 try{
  const cfg=await fetch("/api/config").then(r=>r.json());
  if(!cfg.ready)throw new Error("إعدادات تسجيل الدخول غير مكتملة");
  supabaseClient=window.supabase.createClient(cfg.supabase_url,cfg.supabase_anon_key);
  const {data}=await supabaseClient.auth.getSession();
  if(!data.session){location.href="/";return;}
  accessToken=data.session.access_token;
  await api("/api/access/check");
  try{await loadCloseSettings();}catch(settingsError){$("closeSettingsMessage").textContent=settingsError.message;}
  await refresh();
 }catch(e){$("serviceState").textContent="يتطلب تسجيل الدخول";$("dataNotice").innerHTML="<b>تعذر التحقق من الحساب</b><span>"+esc(e.message)+" — ارجع إلى صفحة الدخول.</span>";}
}
$("refreshBtn").addEventListener("click",refresh);
$("saveCloseSettings").addEventListener("click",saveCloseSettings);
$("saveRecoverySettings").addEventListener("click",saveRecoverySettings);
$("saveProtectionSettings").addEventListener("click",saveProtectionSettings);
$("savePositionManagement").addEventListener("click",savePositionManagement);
$("pmMode").addEventListener("change",updatePmModeUI);
$("stopLossMode").addEventListener("change",updateStopLossHelp);
boot();setInterval(()=>{if(accessToken)refresh();},2000);
