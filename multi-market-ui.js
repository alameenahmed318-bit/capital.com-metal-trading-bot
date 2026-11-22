/* Shared dual-market navigation. A view preference is not a trade permit.
   Only the authenticated gateway may authorize a market selection. */
(()=>{
"use strict";
const allowed=["BTCUSD","XAUUSD"],names={BTCUSD:"البيتكوين",XAUUSD:"الذهب"};
const originalFetch=window.fetch.bind(window);
let viewed="BTCUSD",token="",switchBusy=false;
try{const saved=sessionStorage.getItem("alzool_market");if(allowed.includes(saved))viewed=saved}catch{}
function scoped(path){
 const url=new URL(path,location.origin);
 if(url.origin!==location.origin||viewed!=="XAUUSD"||url.pathname.startsWith("/market/")||
    url.pathname.startsWith("/api/multi-market/"))return path;
 if(url.pathname.startsWith("/api/")||url.pathname==="/strategy-engine.js")return "/market/XAUUSD"+url.pathname+url.search;
 return path;
}
window.fetch=function(input,init){
 if(typeof input==="string"||input instanceof URL)return originalFetch(scoped(String(input)),init);
 if(input instanceof Request){
  const dest=scoped(input.url);
  if(dest!==input.url)return originalFetch(new Request(new URL(dest,location.origin),input),init);
 }
 return originalFetch(input,init);
};
window.ALZOOL_MARKET_VIEW=()=>viewed;
async function auth(){
 if(!window.supabase)return "";
 const cfg=await originalFetch("/api/config").then(r=>r.json());
 if(!cfg.ready)return "";
 const client=window.supabase.createClient(cfg.supabase_url,cfg.supabase_anon_key);
 const {data}=await client.auth.getSession();return data.session?.access_token||"";
}
function saveView(market){
 try{sessionStorage.setItem("alzool_market",market)}catch{}
}
function showMessage(s){const e=document.getElementById("mm-market-state");if(e)e.textContent=s}
async function gateway(path,options={}){
 const r=await originalFetch(path,{...options,headers:{"Authorization":"Bearer "+token,"Content-Type":"application/json",...(options.headers||{})}});
 return {ok:r.ok,data:await r.json().catch(()=>({}))};
}
function draw(data){
 for(const m of allowed){
  const button=document.getElementById("mm-"+m);if(!button)continue;
  const state=data?.markets?.[m]||{};
  button.querySelector("small").textContent=state.new_entries_allowed?(state.trading_enabled?"مختار · التداول مفعّل":"مختار · مفتاح التداول متوقف"):state.ready?"MT5 جاهز":state.paired?"MT5 غير جاهز":"يحتاج ربط MT5";
  button.classList.toggle("is-active",data.active===m&&state.new_entries_allowed===true);
  button.classList.toggle("is-view",viewed===m);
 }
 if(data.switching)showMessage("جارٍ انتقال صلاحيات الدخول؛ إدارة الصفقات المفتوحة مستمرة وفق الإعدادات.");
 else if(data.active&&data.markets?.[data.active]?.new_entries_allowed)showMessage("سوق الدخول المختار: "+names[data.active]+" — السوق المعروض: "+names[viewed]);
 else showMessage("لا يوجد سوق جاهز مصرح له بالدخول. اختر السوق بعد ربط MT5.");
}
async function update(){
 if(switchBusy||!token)return;
 try{const {ok,data}=await gateway("/api/multi-market/status");if(ok)draw(data);else showMessage("تعذر التحقق من حالة السوق")}catch{showMessage("بوابة السوق غير متاحة")}
}
async function choose(market){
 if(switchBusy)return;switchBusy=true;
 const buttons=[...document.querySelectorAll("#mm-selector button")];buttons.forEach(b=>b.disabled=true);
 try{
  const {ok,data}=await gateway("/api/multi-market/select",{method:"POST",body:JSON.stringify({market})});
  if(!ok){
   // Viewing an unpaired market lets the owner open its own pairing page.
   // Never claim that the rejected selection enabled execution.
   saveView(market);sessionStorage.setItem("alzool_market_notice",String(data.error||"عرض فقط: لم يتم تفعيل التداول"));
  }else saveView(market);
  location.reload();
 }catch(e){showMessage("تعذر تبديل السوق، لم يتم تأكيد أي تفعيل");switchBusy=false;buttons.forEach(b=>b.disabled=false)}
}
function updateChrome(){
 document.documentElement.dataset.market=viewed;
 const page=location.pathname;
 if(viewed==="XAUUSD"){
  document.title=document.title.replace(/BTCUSD|BTC|BITCOIN/gi,"GOLD");
  for(const el of document.querySelectorAll(".ss-meta b,.ss-meta span,.ss-menu-footer,.ss-foot span,.ss-menu a span,.ss-hero-right .ss-account span,.eyebrow,.chart-foot")){
   const value=el.textContent;
   if(!el.children.length)el.textContent=value.replace(/البيتكوين/g,"الذهب").replace(/بيتكوين/g,"ذهب").replace(/BTCUSD/g,"XAUUSD").replace(/BTC MARKET|BTC ·|· BTC|BITCOIN/gi,"GOLD").replace(/₿/g,"◆");
  }
  if(/dashboard\.html$/.test(page)){
   for(const id of ["shadowMarketSymbol","marketChartTitle"]){const el=document.getElementById(id);if(el)el.textContent=id==="marketChartTitle"?"XAUUSD · M1":"XAUUSD"}
   document.querySelectorAll(".ss-coin").forEach(el=>el.textContent="◆");
  }
 }
}
async function mount(){
 updateChrome();
 try{
 const node=document.createElement("section");node.id="mm-selector";node.setAttribute("aria-label","اختيار سوق التداول");
 node.innerHTML='<div class="mm-options"><button type="button" id="mm-XAUUSD"><b>◆ الذهب · XAUUSD</b><small>جارٍ الفحص</small></button><button type="button" id="mm-BTCUSD"><b>₿ البيتكوين · BTCUSD</b><small>جارٍ الفحص</small></button></div><div class="mm-info"><b id="mm-market-state">جارٍ التحقق</b><a href="/?manage=1#ownerPairPanel">ربط السوق المعروض مع MT5 ↗</a></div>';
 const style=document.createElement("style");style.textContent='#mm-selector{direction:rtl;display:flex;align-items:center;justify-content:space-between;gap:10px;flex-wrap:wrap;margin:0 0 8px;padding:7px 9px;border:1px solid #3566ae;border-radius:8px;background:linear-gradient(115deg,#08172e,#170b32);box-shadow:0 0 9px #236dff33;color:#edf4ff}#mm-selector .mm-options{display:flex;gap:6px;flex:1}#mm-selector button{display:flex;flex-direction:column;align-items:flex-start;gap:3px;flex:1;min-width:0;background:#08132a;color:#dce8ff;border:1px solid #3d5c91;padding:7px 10px;border-radius:6px;font-size:12px;cursor:pointer}#mm-selector button.is-view{border-color:#b076ff;box-shadow:0 0 9px #a14fff77}#mm-selector button.is-active{border-color:#33ef82}#mm-selector button small{font-size:10px;color:#9eb7d9}#mm-selector button#mm-XAUUSD b{color:#f3cc69}#mm-selector button#mm-BTCUSD b{color:#ffae56}#mm-selector .mm-info{display:flex;flex-direction:column;gap:4px;font-size:11px;min-width:0}#mm-selector a{color:#bb9aff}#mm-selector button:disabled{opacity:.6}@media(max-width:650px){#mm-selector .mm-options{flex-basis:100%}#mm-selector .mm-info{font-size:10px}}';
 document.head.append(style);
 const hero=document.querySelector(".ss-market-strip,.ss-hero,.hero"),main=document.querySelector(".ss-studio")||document.body;
 if(hero)hero.after(node);else main.prepend(node);
 for(const m of allowed){const btn=document.getElementById("mm-"+m);btn.disabled=true;btn.addEventListener("click",()=>choose(m));}
 token=await auth();
 if(!token){showMessage("سجّل الدخول لعرض حالة السوقين.");return}
 const initial=await gateway("/api/multi-market/status");
 if(!initial.ok){showMessage("لم يكتمل التحقق من الربط؛ لا يمكن تفعيل أي سوق.");return}
 for(const m of allowed)document.getElementById("mm-"+m).disabled=false;
 draw(initial.data);
 try{const notice=sessionStorage.getItem("alzool_market_notice");if(notice){showMessage(notice+" — عرض "+names[viewed]+" فقط");sessionStorage.removeItem("alzool_market_notice")}}catch{}
 setInterval(update,5000);
 }catch{showMessage("تعذر التحقق من اتصال المحرك؛ اختيار السوق لا يفعّل التداول.")}
}
if(document.readyState==="loading")document.addEventListener("DOMContentLoaded",()=>setTimeout(mount,100));else setTimeout(mount,100);
})();
