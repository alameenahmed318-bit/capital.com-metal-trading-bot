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
 $("pmMode").value=pm.mode==="MANUAL"?"MANUAL":"AI";
 $("addonEnabled").checked=!!pm.addon_enabled;
 $("addonMax").value=Number(pm.addon_max??0);
 $("addonLot").value=Number(pm.addon_lot??0.01);
 $("addonDistance").value=Number(pm.addon_distance_points??100);
 $("coolingEnabled").checked=!!pm.cooling_enabled;
 $("coolingMax").value=Number(pm.cooling_max??0);
 $("coolingLot").value=Number(pm.cooling_lot??0.01);
 $("coolingDistance").value=Number(pm.cooling_distance_points??100);
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
  addon_enabled:$("addonEnabled").checked,addon_max:Number($("addonMax").value),
  addon_lot:Number($("addonLot").value),addon_distance_points:Number($("addonDistance").value),
  cooling_enabled:$("coolingEnabled").checked,cooling_max:Number($("coolingMax").value),
  cooling_lot:Number($("coolingLot").value),cooling_distance_points:Number($("coolingDistance").value)};
 for(const key of ["addon_max","cooling_max"]){
  if(!Number.isInteger(cfg[key])||cfg[key]<0||cfg[key]>100){$("pmMessage").textContent="الحد الأقصى يجب أن يكون عدداً صحيحاً من 0 إلى 100.";return;}
 }
 for(const key of ["addon_lot","cooling_lot"]){
  if(!Number.isFinite(cfg[key])||cfg[key]<=0||cfg[key]>100){$("pmMessage").textContent="تحقق من قيم اللوت اليدوي.";return;}
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
  await api("/api/trade-management/settings",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({position_management:cfg})});
  $("pmMessage").textContent="تم الحفظ. سيستلم MT5 الإعدادات خلال دورة التحديث التالية.";
 }catch(e){$("pmMessage").textContent="تعذر الحفظ: "+e.message;}
 finally{$("savePositionManagement").disabled=false;}
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
  renderOrders(Array.isArray(s.orders)?s.orders:[],currency);
  $("lastSeen").textContent=timeText(s.last_seen);
  $("serviceState").textContent=s.connected?"بيانات MT5 مباشرة":"بانتظار اتصال MT5";
  if(s.connected){$("dataNotice").innerHTML="<b>MT5 متصل</b><span>آخر تحديث: "+esc(timeText(s.last_seen))+" · تتم قراءة المراكز والأوامر مباشرة دون إرسال أوامر تداول.</span>";}
  else{$("dataNotice").innerHTML="<b>MT5 غير متصل</b><span>لم تصل بيانات حديثة. افتح الطرفية وتأكد من تشغيل EA واتصال الترخيص.</span>";}
 }catch(e){$("serviceState").textContent="تعذر تحميل البيانات";$("dataNotice").innerHTML="<b>تعذر تحديث بيانات التداول</b><span>"+esc(e.message)+"</span>";$("floatingMeta").textContent=e.message;}
 finally{refreshBusy=false;$("refreshBtn").disabled=false;}
}
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
$("saveProtectionSettings").addEventListener("click",saveProtectionSettings);
$("savePositionManagement").addEventListener("click",savePositionManagement);
$("pmMode").addEventListener("change",updatePmModeUI);
$("stopLossMode").addEventListener("change",updateStopLossHelp);
boot();setInterval(()=>{if(accessToken)refresh();},2000);
