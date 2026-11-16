/* AL-ZOOL PRIME: single-link shell over the existing authenticated modules.
 * Real widgets and forms remain owned by their original pages; no trading state
 * or settings are faked, stored or modified by this navigation component.
 */
(()=>{
"use strict";
const CONFIG={
 home:{label:"الرئيسية",title:"مركز القيادة",subtitle:"الحساب، السوق، حالة MT5 وقرارات المحرك",source:"لوحة القيادة المباشرة",url:"/dashboard.html",tabs:[]},
 trading:{label:"التداول",title:"التداول وإدارة الصفقات",subtitle:"الصفقات، الوقف، التعزيز، التبريد والتعافي في مكان واحد",source:"مركز إدارة الصفقات",url:"/trade-management.html",tabs:[
  ["positions","الصفقات المفتوحة","#positions"],["profits","جني الأرباح","#autoClose"],
  ["protection","الحماية والوقف","#protection"],["addon","التعزيز والتبريد","#positionManagement"],
  ["recovery","التعافي الذكي","#smartRecovery"],["pending","الأوامر المعلقة","#pending"]]},
 market:{label:"السوق",title:"السوق والفرص",subtitle:"إشارات الذهب وفرصه ومعلومات الاستراتيجيات",source:"مختبر فرص الذهب",url:"/opportunities.html",tabs:[]},
 ai:{label:"الذكاء",title:"الذكاء ومجلس التصويت",subtitle:"Fast AI، Qwen، DeepSeek وتشخيص أسباب القرار",source:"التحليل ومجلس الذكاء",url:"/ai-analysis.html",tabs:[
  ["analysis","التحليل المباشر","#aiResult"],["diagnostics","تشخيص القرار","#diagPanel"],
  ["monitor","مراقبة Hybrid AI","/ai-monitor.html"]]},
 monitor:{label:"المراقبة",title:"مراقبة قرارات Hybrid AI",subtitle:"أسباب BUY وSELL وWAIT وأثر Fast AI وQwen وDeepSeek والبوتات والتراكيب",source:"المراقبة الفعلية للمحرك",url:"/ai-monitor.html",tabs:[]},
 research:{label:"الاستراتيجيات",title:"الاستراتيجيات والتعلّم",subtitle:"نتائج التعلم وتدقيق الاستراتيجيات والاختبارات",source:"مركز البحث والتعلّم",url:"/learning-lab.html",tabs:[
  ["learning","التعلم والذاكرة","/learning-lab.html"],["audit","تدقيق الاستراتيجيات","/learning-lab.html#strategyAuditPanel"],
  ["backtest","الاختبارات التاريخية","/backtest-lab.html"]]},
 bots:{label:"البوتات العالمية",title:"مكتبة البوتات العالمية",subtitle:"مقارنة بوتات الذهب العالمية ودراسة أفضل الأفكار لـ AL-ZOOL",source:"المكتبة المرجعية المستقلة",url:"/bots-library.html",tabs:[]},
  reports:{label:"التقارير",title:"الأداء والتقارير",subtitle:"الصفقات المغلقة وسجلات التنفيذ والنتائج",source:"سجل التداول",url:"/trade-history.html",tabs:[
  ["history","الصفقات المغلقة","#tradesTable"],["events","أحداث التنفيذ","#tradeEventsTable"],["performance","أداء الحساب","/dashboard.html#accountPerformance"]]},
 admin:{label:"الإدارة",title:"العملاء والتراخيص",subtitle:"المالك، العملاء، تراخيص الاتصال وربط MT5",source:"بوابة الاتصال والتراخيص",url:"/",tabs:[
  ["overview","لوحة المالك","#adminOverview"],["clients","العملاء","#adminClients"],
  ["license","ربط MT5","#ownerPairPanel"],["engine","محرك التداول","#actionPanel"]]},
 settings:{label:"الإعدادات",title:"الإعدادات والحماية",subtitle:"تكوين التداول وإدارة المخاطر والربط",source:"إعدادات المحرك",url:"/trade-management.html",tabs:[
  ["trading","إعدادات التداول","#positionManagement"],["risk","الحماية والوقف","#protection"],
  ["recovery","إعدادات التعافي","#smartRecovery"],["policy","قرار Hybrid AI","#entryPolicy"],["connections","الربط والتراخيص","/#ownerPairPanel"]]}
};
const $=id=>document.getElementById(id);
const frame=$("primeFrame"),tabs=$("primeTabs"),loading=$("primeLoading"),
 sidebar=$("primeSidebar"),overlay=$("primeOverlay"),mobile=$("primeMobileMenu");
let activeSection="home",activeTab="",currentUrl="/dashboard.html",latestLoaded="",embeddedReady=false;
const normalizeUrl=(raw)=>{
 try{
  const url=new URL(raw,location.origin);
  if(url.origin!==location.origin)return null;
  if(!["/","/index.html","/dashboard.html","/trade-management.html","/opportunities.html","/ai-analysis.html","/ai-monitor.html","/learning-lab.html","/backtest-lab.html","/trade-history.html","/bots-library.html"].includes(url.pathname))return null;
  return url.pathname+url.hash;
 }catch{return null;}
};
function innerPath(section,tab){
 const group=CONFIG[section];if(!group)return "/dashboard.html";
 const selected=group.tabs.find(item=>item[0]===tab);
 if(!selected)return group.url;
 const u=selected[2];
 return u.startsWith("#")?group.url+u:u;
}
function matchRoute(url){
 const path=url.split("#")[0],hash=url.includes("#")?"#"+url.split("#").slice(1).join("#"):"";
 if(path==="/dashboard.html"){
  if(hash==="#accountPerformance")return ["reports","performance"];
  return ["home",""];
 }
 if(path==="/trade-management.html"){
  const lookup={"#positions":["trading","positions"],"#autoClose":["trading","profits"],
   "#protection":["trading","protection"],"#positionManagement":["trading","addon"],
   "#smartRecovery":["trading","recovery"],"#pending":["trading","pending"],
   "#entryPolicy":["settings","policy"]};
  return lookup[hash]||["trading","positions"];
 }
 if(path==="/opportunities.html")return ["market",""];
 if(path==="/ai-analysis.html")return ["ai",hash==="#diagPanel"?"diagnostics":"analysis"];
 if(path==="/ai-monitor.html")return ["monitor",""];
 if(path==="/learning-lab.html")return ["research",hash==="#strategyAuditPanel"?"audit":"learning"];
 if(path==="/backtest-lab.html")return ["research","backtest"];
 if(path==="/bots-library.html")return ["bots",""];
 if(path==="/trade-history.html")return ["reports",hash==="#tradeEventsTable"?"events":"history"];
 if(path==="/"||path==="/index.html"){
  return ["admin",{"#adminClients":"clients","#ownerPairPanel":"license","#actionPanel":"engine"}[hash]||"overview"];
 }
 return ["home",""];
}
function encodeRoute(section,tab){return "#"+section+(tab?"/"+tab:"");}
function readHash(){
 const parts=decodeURIComponent(location.hash.replace(/^#/,"")).split("/");
 const section=CONFIG[parts[0]]?parts[0]:"home";
 const tab=CONFIG[section].tabs.some(x=>x[0]===parts[1])?parts[1]:"";
 return [section,tab];
}
function showMenu(state){
 const open=!!state;sidebar.classList.toggle("open",open);
 overlay.hidden=!open;mobile.setAttribute("aria-expanded",String(open));
}
function renderTabs(section,tab){
 tabs.replaceChildren();
 for(const [id,label] of CONFIG[section].tabs){
  const b=document.createElement("button");b.type="button";b.textContent=label;
  b.className="az-tab"+(id===tab?" active":"");
  b.setAttribute("aria-current",id===tab?"page":"false");
  b.addEventListener("click",()=>navigate(section,id));
  tabs.appendChild(b);
 }
 tabs.hidden=!CONFIG[section].tabs.length;
}
function navigate(section,tab="",updateHistory=true){
 const config=CONFIG[section]||CONFIG.home;
 section=CONFIG[section]?section:"home";
 tab=config.tabs.some(x=>x[0]===tab)?tab:config.tabs[0]?.[0]||"";
 const requested=innerPath(section,tab);
 const previousUrl=currentUrl;
 activeSection=section;activeTab=tab;
 $("primeTitle").textContent=config.title;
 $("primeSubtitle").textContent=config.subtitle;
 $("primeBreadcrumb").textContent=config.label;
 $("primeWorkspaceTitle").textContent=config.label;
 $("primeActiveSource").textContent=config.source;
 for(const nav of document.querySelectorAll(".az-nav-item")){
  const active=nav.dataset.section===section;
  nav.classList.toggle("active",active);nav.setAttribute("aria-current",active?"page":"false");
 }
 renderTabs(section,tab);
 showMenu(false);
 if(updateHistory){const route=encodeRoute(section,tab);if(location.hash!==route)history.pushState(null,"",route)}
 currentUrl=requested;
 if(requested.split("#")[0]===previousUrl.split("#")[0]&&embeddedReady){
  try{
   const hash=requested.includes("#")?"#"+requested.split("#").slice(1).join("#"):"";
   if(hash)frame.contentWindow.location.hash=hash;
   else frame.contentWindow.history.replaceState(null,"",requested.split("#")[0]);
   const dest=hash.slice(1);
   if(dest)frame.contentDocument?.getElementById(dest)?.scrollIntoView({block:"start"});
  }catch{}
  return;
 }
 loading.hidden=false;
 embeddedReady=false;
 // Browser keeps authentication/session handling with the original same-origin module.
 frame.src=requested;
}
function applyEmbeddedStyle(doc){
 if(!doc||doc.getElementById("alzool-prime-embedded"))return;
 doc.documentElement.classList.add("az-prime-embedded-root");
 const theme=doc.createElement("link");
 theme.id="alzool-prime-embedded";
 theme.rel="stylesheet";
 theme.href="/prime-embedded.css?v=sudan-20261115-3";
 (doc.head||doc.documentElement).appendChild(theme);
 const style=doc.createElement("style");style.id="alzool-prime-structural";
 style.textContent=`
  /* Embedded module mode — hides only duplicate layout chrome. */
  html,body{min-height:100%!important;background:#0c1018!important}
  .shell,.shell.ui-collapsed{display:block!important;min-height:0!important;grid-template-columns:1fr!important}
  .shell>.sidebar,.shell>.az-mobile-bar,.az-nav-overlay,.az-page-shortcuts,
  .az-admin-layout>.az-admin-sidebar,.az-owner-mobile-bar,.az-owner-overlay{display:none!important}
  .main,.shell>.main,.az-admin-content{width:100%!important;max-width:none!important;margin:0!important;padding:20px 16px 30px!important}
  .az-admin-layout{display:block!important;min-height:0!important;grid-template-columns:1fr!important}
  .az-admin-content{display:block!important}
  .az-admin-content>.az-owner-shortcuts{display:flex!important}
  body:has(#adminView:not(.hidden))>.wrap{max-width:none!important;padding:0!important;margin:0!important}
  body:has(#adminView:not(.hidden))>.wrap>.top{display:none!important}
  .ui-sidebar-toggle{display:none!important}
  .topbar{padding-top:0!important}
  footer{margin-top:24px!important}
  @media(max-width:900px){
   .shell>.sidebar,.shell>.az-mobile-bar,.az-nav-overlay,
   .az-admin-layout>.az-admin-sidebar,.az-owner-mobile-bar,.az-owner-overlay{display:none!important}
   .main,.shell>.main,.az-admin-content{padding:14px 10px 24px!important}
   .az-admin-layout{display:block!important}
  }`;
 (doc.head||doc.documentElement).appendChild(style);
}
function attachInnerLinks(doc){
 if(doc.documentElement.dataset.primeLinksAttached)return;
 doc.documentElement.dataset.primeLinksAttached="true";
 doc.addEventListener("click",event=>{
  const link=event.target.closest?.("a[href]");
  if(!link||event.defaultPrevented||event.ctrlKey||event.metaKey||event.shiftKey||event.altKey)return;
  const href=link.getAttribute("href")||"";
  if(href.startsWith("#")||link.hasAttribute("download"))return;
  const url=normalizeUrl(link.href);if(!url)return;
  event.preventDefault();
  const [group,tab]=matchRoute(url);
  navigate(group,tab);
 },false);
}
frame.addEventListener("load",()=>{
 try{
  const actual=normalizeUrl(frame.contentWindow.location.href);
  if(actual){
   applyEmbeddedStyle(frame.contentDocument);
   attachInnerLinks(frame.contentDocument);
  }
  embeddedReady=true;loading.hidden=true;
 }catch{
  loading.hidden=true;
  embeddedReady=false;
 }
});
frame.addEventListener("error",()=>{loading.hidden=true;embeddedReady=false});
document.getElementById("primeNav").addEventListener("click",event=>{
 const item=event.target.closest("button[data-section]");
 if(item)navigate(item.dataset.section);
});
document.querySelectorAll("a[data-section]").forEach(a=>a.addEventListener("click",e=>{
 e.preventDefault();navigate(a.dataset.section);
}));
document.getElementById("primeModules").addEventListener("click",event=>{
 const btn=event.target.closest("[data-go]");if(btn)navigate(btn.dataset.go,btn.dataset.tab||"");
});
const filter=$("primeSearch");
filter.addEventListener("input",()=>{
 const q=filter.value.trim().toLocaleLowerCase("ar");let shown=0;
 for(const b of document.querySelectorAll(".az-nav-item")){
  const visible=!q||b.textContent.toLocaleLowerCase("ar").includes(q);
  b.hidden=!visible;if(visible)shown++;
 }
 $("primeNoResults").hidden=shown!==0;
 for(const group of document.querySelectorAll(".az-nav-caption")){
  let next=group.nextElementSibling,has=false;
  while(next&&next.classList.contains("az-nav-item")){if(!next.hidden)has=true;next=next.nextElementSibling;}
  group.hidden=!has;
 }
});
mobile.addEventListener("click",()=>showMenu(!sidebar.classList.contains("open")));
overlay.addEventListener("click",()=>showMenu(false));
document.addEventListener("keydown",e=>{
 if(e.key==="Escape")showMenu(false);
 if((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==="k"){
  e.preventDefault();showMenu(true);filter.focus();filter.select();
 }
});
window.addEventListener("popstate",()=>{const [a,b]=readHash();navigate(a,b,false)});
window.addEventListener("hashchange",()=>{const [a,b]=readHash();if(a!==activeSection||b!==activeTab)navigate(a,b,false)});
async function checkServer(){
 const controller=new AbortController();const timer=setTimeout(()=>controller.abort(),5000);
 try{
  const response=await fetch("/health",{cache:"no-store",signal:controller.signal});
  const body=await response.json();
  if(!response.ok||body.status!=="UP")throw Error("unavailable");
  $("primePulse").className="az-pulse up";
  document.querySelector(".az-chip-led").className="az-chip-led up";
  $("primeServerStatus").textContent="الخادم يعمل — حالة MT5 داخل الرئيسية";
  $("primeStatusLabel").textContent="الخادم متصل";
 }catch{
  $("primePulse").className="az-pulse down";
  document.querySelector(".az-chip-led").className="az-chip-led down";
  $("primeServerStatus").textContent="تعذر الوصول إلى الخادم";
  $("primeStatusLabel").textContent="الخادم غير متاح";
 }finally{clearTimeout(timer)}
}
const [startSection,startTab]=readHash();
navigate(startSection,startTab,false);
checkServer();
})();
