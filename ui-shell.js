/* AL-ZOOL UI navigation v20261115: central information architecture.
   Navigation only: never changes API, auth, account settings or trading state. */
(()=>{
"use strict";
const shell=document.querySelector(".shell"),sidebar=document.querySelector(".sidebar");
if(!shell||!sidebar)return;
const current=(location.pathname.split("/").pop()||"dashboard.html").toLowerCase();
const groups=[
 {title:"الرئيسية والمتابعة",icon:"◫",items:[
  ["dashboard.html","⌂","لوحة القيادة","الحساب، السوق، حالة MT5"],
  ["opportunities.html","◎","الفرص والإشارات","فرص الذهب وأسباب القرار"]]},
 {title:"الصفقات والتنفيذ",icon:"⇄",items:[
  ["trade-management.html","⇄","الصفقات والحماية","المراكز، الوقف، الأوامر، التعافي"],
  ["trade-history.html","▤","السجل والأداء","الصفقات المغلقة والنتائج"]]},
 {title:"الذكاء والاستراتيجيات",icon:"✦",items:[
  ["ai-analysis.html","✧","التحليل ومجلس الذكاء","تصويت النماذج والتشخيص"],
  ["learning-lab.html","◈","التعلّم والاستراتيجيات","الأوزان وذاكرة النتائج"],
  ["backtest-lab.html","⌁","الاختبارات التاريخية","الشموع والتجارب"]]},
 {title:"العملاء والإعدادات",icon:"⚙",items:[
  ["","⚙","بوابة المالك والعملاء","الربط والتراخيص والإدارة"]]}
];
const sections={
 "dashboard.html":[["liveStatus","حالة MT5"],["aiCouncilLive","مجلس الذكاء"],["tradingSettingsPanel","إعدادات التداول"],["accountPerformance","أداء الحساب"],["closedHistory","آخر الصفقات"]],
 "trade-management.html":[["positions","الصفقات المفتوحة"],["protection","الحماية والوقف"],["autoClose","جني الأرباح"],["positionManagement","التعزيز والتبريد"],["smartRecovery","التعافي الذكي"],["pending","الأوامر المعلقة"],["recoveryLog","سجل التعافي"]],
 "ai-analysis.html":[["aiResult","نتيجة التحليل"],["diagPanel","التشخيص"]],
 "learning-lab.html":[["strategyAuditPanel","تدقيق الاستراتيجيات"],["liveStatus","التعلّم المباشر"],["runsTable","جولات الاختبار"],["learningStatus","نتائج التعلّم"]],
 "opportunities.html":[["marketStatus","السوق الآن"],["topStrategies","أقوى الاستراتيجيات"],["topCombinations","أفضل التركيبات"],["eventTable","سجل الإشارات"]],
 "backtest-lab.html":[["methodNote","إعدادات الاختبار"],["strategyResults","نتائج الاستراتيجيات"],["comboResults","نتائج التركيبات"]],
 "trade-history.html":[["historyStatus","ملخص النتائج"],["tradesTable","الصفقات المغلقة"],["tradeEventsTable","أحداث التداول"]]
};
const slug=page=>page?"/"+page:"/";
const $=(tag,cls,txt)=>{let e=document.createElement(tag);if(cls)e.className=cls;if(txt!=null)e.textContent=txt;return e};
const statusText=sidebar.querySelector("#serviceState")?.textContent||"";
const brand=$("a","brand");brand.href="/dashboard.html";
brand.innerHTML='<span class="brand-mark" aria-hidden="true">AZ</span><span><b>AL-ZOOL</b><small>GOLD CONTROL CENTER</small></span>';
const top=$("div","az-nav-head");
const mobileClose=$("button","az-nav-close","✕");mobileClose.type="button";mobileClose.setAttribute("aria-label","إغلاق القائمة");
top.append(brand,mobileClose);
const intro=$("div","az-nav-intro");
intro.innerHTML='<b>القائمة الرئيسية</b><small>كل قسم في مكان واضح</small>';
const searchWrap=$("div","az-nav-search");searchWrap.setAttribute("role","search");
const search=$("input","az-nav-search-input");search.type="search";search.placeholder="ابحث عن صفحة أو وظيفة…";
search.setAttribute("aria-label","بحث عن صفحة أو وظيفة داخل AL-ZOOL");
search.autocomplete="off";
searchWrap.appendChild(search);
const nav=$("nav","az-nav-menu");nav.setAttribute("aria-label","التنقل الرئيسي");
const nodes=[];
for(const group of groups){
 const category=$("section","az-nav-category");
 const title=$("div","side-label",group.title);category.appendChild(title);
 const links=$("div","nav-group");
 for(const [page,icon,label,description] of group.items){
  const link=$("a","nav"+(page===current?" active":""));
  link.href=slug(page);link.title=description;
  if(page===current)link.setAttribute("aria-current","page");
  const glyph=$("span","az-nav-icon",icon);glyph.setAttribute("aria-hidden","true");
  const labelEl=$("span","nav-label",label);
  link.append(glyph,labelEl);
  links.appendChild(link);
  nodes.push({category,link,search:(label+" "+description+" "+group.title).toLocaleLowerCase("ar")});
 }
 category.appendChild(links);nav.appendChild(category);
}
const empty=$("p","az-nav-empty","ما لقينا صفحة بهذا الاسم.");empty.hidden=true;
nav.appendChild(empty);
const resultsFilter=()=>{
 const q=search.value.trim().toLocaleLowerCase("ar");
 let visible=0;
 for(const node of nodes){const show=!q||node.search.includes(q);node.link.hidden=!show;if(show)visible++;}
 for(const group of nav.querySelectorAll(".az-nav-category"))group.hidden=![...group.querySelectorAll(".nav")].some(x=>!x.hidden);
 empty.hidden=visible!==0;
};
search.addEventListener("input",resultsFilter);
const last=$("div","side-bottom");
last.innerHTML='<span class="live-dot" aria-hidden="true"></span><span>حالة الاتصال</span><small id="serviceState"></small>';
last.querySelector("#serviceState").textContent=statusText;
sidebar.replaceChildren(top,intro,searchWrap,nav,last);
sidebar.id="alzool-site-nav";
sidebar.setAttribute("aria-label","قائمة AL-ZOOL");
const key="alzool-ui-sidebar-collapsed";
let collapsed=false;try{collapsed=localStorage.getItem(key)==="1"}catch{}
const collapse=$("button","ui-sidebar-toggle","⇤ طي القائمة");collapse.type="button";
collapse.setAttribute("aria-label","طي أو توسيع القائمة الجانبية");
sidebar.insertBefore(collapse,nav);
const isMobile=()=>matchMedia("(max-width:900px)").matches;
const toggleCollapse=()=>{
 const compact=collapsed&&!isMobile();shell.classList.toggle("ui-collapsed",compact);
 collapse.textContent=compact?"⇥ توسيع القائمة":"⇤ طي القائمة";
 collapse.setAttribute("aria-expanded",String(!compact));
};
collapse.addEventListener("click",()=>{collapsed=!collapsed;try{localStorage.setItem(key,collapsed?"1":"0")}catch{}toggleCollapse()});
const overlay=$("div","az-nav-overlay");
const mobileTrigger=$("button","az-mobile-trigger","☰  القائمة");mobileTrigger.type="button";
mobileTrigger.setAttribute("aria-controls","alzool-site-nav");mobileTrigger.setAttribute("aria-expanded","false");
const mobileBar=$("div","az-mobile-bar");mobileBar.appendChild(mobileTrigger);
shell.insertBefore(mobileBar,sidebar);
document.body.appendChild(overlay);
const close=()=>{shell.classList.remove("az-menu-open");mobileTrigger.setAttribute("aria-expanded","false");document.body.classList.remove("az-nav-lock")};
const open=()=>{shell.classList.add("az-menu-open");mobileTrigger.setAttribute("aria-expanded","true");document.body.classList.add("az-nav-lock");search.focus()};
mobileTrigger.addEventListener("click",()=>shell.classList.contains("az-menu-open")?close():open());
mobileClose.addEventListener("click",close);overlay.addEventListener("click",close);
nav.addEventListener("click",e=>{if(e.target.closest("a"))close()});
window.addEventListener("resize",()=>{toggleCollapse();if(!isMobile())close()},{passive:true});
document.addEventListener("keydown",event=>{
 if(event.key==="Escape"){close();if(document.activeElement===search)search.blur();}
 if((event.ctrlKey||event.metaKey)&&event.key.toLowerCase()==="k"){
  event.preventDefault();if(isMobile())open();else{search.focus();search.select();}
 }
});
toggleCollapse();
const main=shell.querySelector(".main");
const links=(sections[current]||[]).filter(([id])=>document.getElementById(id));
if(main&&links.length){
 const shortcuts=$("section","az-page-shortcuts");
 shortcuts.setAttribute("aria-label","انتقال سريع داخل الصفحة");
 const heading=$("div","az-shortcuts-heading");
 heading.innerHTML='<strong>اذهب مباشرة إلى</strong><small>اختر القسم الذي تحتاجه بدون بحث بين البطاقات</small>';
 const items=$("div","az-shortcuts-links");
 for(const [id,label] of links){
  const a=$("a","az-shortcut",label);a.href="#"+id;
  a.addEventListener("click",()=>{for(const el of items.children)el.classList.remove("selected");a.classList.add("selected")});
  items.appendChild(a);
 }
 shortcuts.append(heading,items);
 const topbar=main.querySelector(".topbar");
 if(topbar)topbar.insertAdjacentElement("afterend",shortcuts);else main.prepend(shortcuts);
}
// Trade management has many independent control panels. Present one area at
// a time by default; preserve every form element, event handler and API call.
if(current==="trade-management.html"&&main){
 const focusIds=["positions","protection","autoClose","positionManagement","smartRecovery","pending"];
 const cards=focusIds.map(id=>document.getElementById(id)).filter(Boolean);
 const sectionNav=main.querySelector(".section-nav");
 const shortcutSection=main.querySelector(".az-page-shortcuts");
 if(cards.length&&shortcutSection){
  document.body.classList.add("az-focus-page");
  if(sectionNav)sectionNav.classList.add("az-old-section-nav");
  const heading=shortcutSection.querySelector(".az-shortcuts-heading");
  const showAll=$("button","az-show-all","عرض كل الأقسام");showAll.type="button";
  heading?.appendChild(showAll);
  const focus=(id)=>{
   const chosen=id==="recoveryLog"?"smartRecovery":id;
   const all=chosen==="all";
   for(const card of cards){
    card.classList.add("az-focus-section");
    card.hidden=!all&&card.id!==chosen;
   }
   for(const a of shortcutSection.querySelectorAll("a.az-shortcut")){
    a.classList.toggle("selected",!all&&(a.hash.slice(1)===id||a.hash.slice(1)===chosen));
   }
   showAll.classList.toggle("selected",all);
   showAll.textContent=all?"اختر قسماً للتركيز":"عرض كل الأقسام";
  };
  shortcutSection.addEventListener("click",e=>{
   const a=e.target.closest("a.az-shortcut");
   if(!a)return;
   focus(decodeURIComponent(a.hash.slice(1)));
  });
  showAll.addEventListener("click",()=>focus("all"));
  const selected=decodeURIComponent((location.hash||"").replace(/^#/,""));
  focus(focusIds.includes(selected)||selected==="recoveryLog"?selected:"positions");
  window.addEventListener("hashchange",()=>{
   const id=decodeURIComponent(location.hash.slice(1));
   if(focusIds.includes(id)||id==="recoveryLog")focus(id);
  });
 }
}
// Navigate back to the main list when deep-linked to a section.
if(location.hash&&document.getElementById(decodeURIComponent(location.hash.slice(1)))){
 requestAnimationFrame(()=>document.getElementById(decodeURIComponent(location.hash.slice(1)))?.scrollIntoView({block:"start"}));
}
})();