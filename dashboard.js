let supabaseClient=null,accessToken=null;
const $=id=>document.getElementById(id);
async function api(path){
 const r=await fetch(path,{headers:accessToken?{Authorization:"Bearer "+accessToken}:{}});
 const d=await r.json().catch(()=>({}));
 if(!r.ok)throw new Error(d.error||"API "+r.status);
 return d;
}
function setState(id,text,kind){const el=$(id);el.textContent=text;el.className="state "+kind;}
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
  $("openTrades").textContent=s.open_trades==null?"—":fmt(s.open_trades);
  $("winRate").textContent=s.win_rate==null?"—":fmt(s.win_rate)+"%";
  $("balanceMeta").textContent=s.equity==null?"لا توجد بيانات حقوق الحساب":"حقوق الحساب: "+fmt(s.equity)+" "+currency;
  const daily=s.daily||{};
  $("dailyProfit").textContent=daily.profit==null?"—":fmt(daily.profit)+" "+currency;
  $("dailyLoss").textContent=daily.loss==null?"—":fmt(daily.loss)+" "+currency;
  $("dailyNet").textContent=daily.net==null?"—":fmt(daily.net)+" "+currency;
  $("dailyTrades").textContent=daily.trades==null?"—":fmt(daily.trades);
  $("dailyMeta").textContent="إجمالي الصفقات المغلقة: "+fmt(s.closed_count||0)+" · نسبة النجاح من سجل MT5 المستلم";
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
 }
}
$("refreshBtn").addEventListener("click",refresh);
boot();
setInterval(()=>{if(accessToken)refresh();},10000);
