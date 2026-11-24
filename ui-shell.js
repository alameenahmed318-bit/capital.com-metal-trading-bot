/* Single shared Shadow System shell; DOM presentation only. */
(()=>{"use strict";
const HEADER="<header class=\"ss-hero\"><div class=\"ss-hero-art\" aria-hidden=\"true\"></div><div class=\"ss-logo\"><div class=\"ss-logo-line\"><span class=\"ss-crest\">♛</span><strong>AL-ZOOL</strong><span class=\"ss-flag\" role=\"img\" aria-label=\"علم السودان\"></span></div><div class=\"ss-brand-sub\">SUPER AI TRADING STUDIO</div><div class=\"ss-tagline\" dir=\"rtl\">منصة التداول الذكي الشاملة</div></div><div class=\"ss-hero-right\"><div class=\"ss-account\"><i>♛</i><span id=\"azCurrentUser\">الحساب المتصل</span></div><time id=\"clock\" class=\"ss-clock\">—</time><a class=\"jump\" href=\"/?manage=1\">إدارة الحساب</a></div></header>";
const MENU="<aside class=\"ss-menu\" aria-label=\"القائمة الرئيسية\"><a href=\"/dashboard.html\" class=\"active\" aria-current=\"page\"><span>لوحة القيادة</span><i aria-hidden=\"true\">⌂</i></a><a href=\"/ai-analysis.html\"><span>تحليل البيتكوين</span><i aria-hidden=\"true\">◫</i></a><a href=\"/dashboard.html#market\"><span>الشموع</span><i aria-hidden=\"true\">▥</i></a><a href=\"/master-ai.html\"><span>MASTER AI · العقل المركزي</span><i aria-hidden=\"true\">✦</i></a><a href=\"/ai-monitor.html#modelsPanel\"><span>مجلس التصويت</span><i aria-hidden=\"true\">◎</i></a><a href=\"/ai-monitor.html\"><span>التحليل الذكي</span><i aria-hidden=\"true\">✦</i></a><a href=\"/opportunities.html\"><span>مختبر الفرص</span><i aria-hidden=\"true\">◇</i></a><a href=\"/learning-lab.html\"><span>عائلات الاستراتيجيات</span><i aria-hidden=\"true\">▱</i></a><a href=\"/opportunities.html#eventTable\"><span>سجل الفرص</span><i aria-hidden=\"true\">▤</i></a><a href=\"/trade-management.html#positions\"><span>الصفقات المفتوحة</span><i aria-hidden=\"true\">⇄</i></a><a href=\"/trade-management.html#positionManagement\"><span>التعزيز والتبريد</span><i aria-hidden=\"true\">⊕</i></a><a href=\"/trade-management.html#pending\"><span>الأوامر المعلقة</span><i aria-hidden=\"true\">◷</i></a><a href=\"/trade-history.html\"><span>الأداء اليومي</span><i aria-hidden=\"true\">▧</i></a><a href=\"/backtest-lab.html\"><span>الاختبار التاريخي</span><i aria-hidden=\"true\">⬡</i></a><a href=\"/trade-management.html#autoClose\"><span>الإعدادات</span><i aria-hidden=\"true\">⚙</i></a><a href=\"/bots-library.html\"><span>مكتبة البوتات</span><i aria-hidden=\"true\">◇</i></a><a href=\"/?manage=1#ownerPairPanel\"><span>الربط والإدارة</span><i aria-hidden=\"true\">⌁</i></a><div id=\"serviceState\" class=\"ss-menu-footer\">جارٍ فحص الخدمة</div></aside>";
const SECTIONS={"master-ai.html":[["masterDecision","القرار النهائي"],["masterModels","تحليلات النماذج"],["masterManagement","إدارة الصفقات"],["masterLearning","التعلم"],["masterExecution","جاهزية التنفيذ"]],"trade-management.html":[["positions","الصفقات المفتوحة"],["autoClose","جني الأرباح"],["protection","الحماية والوقف"],["entryPolicy","قرار الدخول"],["positionManagement","التعزيز والتبريد"],["smartRecovery","التعافي الذكي"],["pending","الأوامر المعلقة"]],"ai-analysis.html":[["aiResult","نتيجة التحليل"],["diagPanel","التشخيص"]],"ai-monitor.html":[["hybridEffectPanel","قرار Hybrid AI"],["gatePanel","أسباب السماح والمنع"],["modelsPanel","أصوات النماذج"],["familiesPanel","الاستراتيجيات والتراكيب"],["botsPanel","البوتات"],["actionsPanel","التعزيز والتبريد والتعافي"],["eventsPanel","سجل الأحداث"]],"learning-lab.html":[["strategyAuditPanel","تدقيق الاستراتيجيات"],["liveStatus","التعلم المباشر"],["runsTable","جولات الاختبار"],["learningStatus","نتائج التعلم"]],"opportunities.html":[["marketStatus","السوق الآن"],["topStrategies","أقوى الاستراتيجيات"],["topCombinations","أفضل التركيبات"],["eventTable","سجل الإشارات"]],"backtest-lab.html":[["methodNote","إعدادات الاختبار"],["strategyResults","نتائج الاستراتيجيات"],["comboResults","نتائج التركيبات"]],"trade-history.html":[["historyStatus","ملخص النتائج"],["tradesTable","الصفقات المغلقة"],["tradeEventsTable","أحداث التداول"]],"bots-library.html":[["activeAdvisors","البوتات التحليلية"],["botsHeading","المكتبة المرجعية"],["ideasHeading","الأفكار"]]};

// Wrap only actual overflowing tables that do not already have a local scroll host.
// Do not clone nodes, replace their IDs, or touch protected content visibility.
const TABLE_WRAPPERS=".lab-table-wrap,.op-table-wrap,.learn-table-wrap,.th-table-wrap,.table-scroll,.table-wrap,.ss-table-scroll,.scroll";
const installTableContainment=()=>{
 const root=document.querySelector(".ss-studio");if(!root)return;
 let scheduled=false;
 const wrap=()=>{
  scheduled=false;
  for(const table of root.querySelectorAll("table")){
   if(table.closest(TABLE_WRAPPERS))continue;
   const parent=table.parentElement;if(!parent)continue;
   const holder=document.createElement("div");holder.className="ss-table-scroll";holder.tabIndex=0;holder.setAttribute("aria-label","جدول قابل للتمرير أفقياً");holder.setAttribute("role","region");
   parent.insertBefore(holder,table);holder.append(table);
  }
 };
 const observer=new MutationObserver(()=>{if(!scheduled){scheduled=true;requestAnimationFrame(wrap)}});
 observer.observe(root,{childList:true,subtree:true});wrap();
};

const page=(location.pathname.split("/").pop()||"index.html").toLowerCase();
const mk=(markup)=>{const template=document.createElement("template");template.innerHTML=markup.trim();return template.content.firstElementChild;};
const hash=()=>{try{return decodeURIComponent(location.hash.slice(1))}catch{return ""}};
const refreshActive=()=>{const all=[...document.querySelectorAll(".ss-menu a")];const href=location.pathname+location.search+location.hash;const actual=page==="index.html"?"/":"/"+page;let exact=all.find(a=>{const u=new URL(a.href,location.origin);return u.pathname+u.search+u.hash===href});if(!exact)exact=all.find(a=>{const u=new URL(a.href,location.origin);return u.pathname===actual&&!u.hash;});if(!exact)exact=all.find(a=>{const u=new URL(a.href,location.origin);return u.pathname===actual});for(const a of all){a.classList.toggle("active",a===exact);if(a===exact)a.setAttribute("aria-current","page");else a.removeAttribute("aria-current")}};
const buildHeader=(label)=>{const el=mk(HEADER);el.querySelector("#azCurrentUser").removeAttribute("id");el.querySelector(".ss-account span").textContent=label;el.querySelector("#clock").id="ssSharedClock";return el};
const buildMenu=(status)=>{const el=mk(MENU);const node=el.querySelector("#serviceState");if(status){status.className="ss-menu-footer";node.replaceWith(status)}else{node.removeAttribute("id");node.textContent="AL-ZOOL · SHADOW SYSTEM"}return el};
const meta=(title)=>mk('<div class="ss-meta"><b>₿ BTC · SHADOW SYSTEM</b><span dir="rtl">'+title+'</span><a href="/dashboard.html">لوحة القيادة ↗</a></div>');
const tick=()=>{const el=document.getElementById("ssSharedClock");if(el)el.textContent=new Intl.DateTimeFormat("en-GB",{timeZone:"Asia/Dubai",dateStyle:"medium",timeStyle:"short"}).format(new Date())+" · UAE"}; 
if(document.getElementById("shadow-studio-v2")){installTableContainment();refreshActive();window.addEventListener("hashchange",refreshActive);return}
if(page==="index.html"){
 const original=document.querySelector("main.wrap");if(!original)return;
 const studio=document.createElement("div");studio.className="ss-studio ss-portal";original.before(studio);studio.append(original);
 const top=original.querySelector("header.top");const header=buildHeader("بوابة النظام");const status=document.getElementById("connection");
 if(status){status.classList.add("ss-portal-status");header.querySelector(".ss-hero-right").prepend(status);}
 if(top)top.replaceWith(header);else original.prepend(header);
 header.after(meta("الاتصال والإدارة"));
 const admin=document.getElementById("adminView"),old=admin?.querySelector(".az-admin-sidebar"),content=admin?.querySelector(".az-admin-content");
 if(admin&&old&&content){
 const menu=buildMenu();const logout=document.getElementById("adminSidebarLogoutBtn");if(logout)menu.append(logout);old.replaceWith(menu);
 const links=[["adminOverview","نظرة عامة"],["ownerPairPanel","ربط MT5"],["adminClients","إدارة العملاء"],["actionPanel","إدارة الصفقات"],["learningPanel","التعلم"],["councilPanel","مجلس الذكاء"],["versionsPanel","الاختبارات"]];
 const tabs=document.createElement("nav");tabs.className="ss-admin-tabs";for(const [id,title] of links){const a=document.createElement("a");a.href="#"+id;a.textContent=title;tabs.append(a)}const welcome=content.querySelector(".welcome");if(welcome)welcome.after(tabs);else content.prepend(tabs);
 }
 const client=document.getElementById("clientView");if(client){const link=document.createElement("a");link.className="jump";link.href="/dashboard.html";link.textContent="العودة إلى لوحة القيادة";client.prepend(link)}
 installTableContainment();tick();setInterval(tick,30000);refreshActive();window.addEventListener("hashchange",refreshActive);return
}
const shell=document.querySelector(".shell");let main=shell?.querySelector(".main");
const bots=page==="bots-library.html";
if(!main&&bots)main=document.querySelector("main.wrap");
if(!main)return;
const studio=document.createElement("div");studio.className="ss-studio ss-secondary";
const workspace=document.createElement("div");workspace.className="ss-workspace";
const title=(document.title.split("|").pop()||"مركز التداول").trim();
const status=document.getElementById("serviceState");
const menu=buildMenu(status);
const anchor=shell||main;anchor.before(studio);studio.append(buildHeader("AL-ZOOL · BTC"),meta(title),workspace);workspace.append(menu,main);
if(shell)shell.remove();if(bots){main.classList.add("ss-bots-content");main.classList.remove("wrap")}else main.classList.add("main");
const items=(SECTIONS[page]||[]).filter(([id])=>document.getElementById(id)); 
if(items.length){const old=main.querySelector(".section-nav");const tabs=document.createElement("nav");tabs.className="ss-section-tabs";tabs.setAttribute("aria-label","أقسام الصفحة");for(const [id,label] of items){const a=document.createElement("a");a.href="#"+id;a.textContent=label;tabs.append(a)}if(old)old.replaceWith(tabs);else {const top=main.querySelector(".topbar")||main.querySelector(".hero");if(top)top.after(tabs);else main.prepend(tabs)}
if(page==="trade-management.html"){
 const cards=items.map(([id])=>document.getElementById(id));const all=document.createElement("button");all.type="button";all.textContent="عرض كل الأقسام";tabs.append(all);
 const focus=(id)=>{const selected=id==="recoveryLog"?"smartRecovery":id;for(const card of cards){card.classList.add("ss-focus");card.classList.toggle("ss-focus-hidden",selected!=="all"&&card.id!==selected)}for(const a of tabs.querySelectorAll("a"))a.classList.toggle("active",a.hash==="#"+selected);all.textContent=selected==="all"?"كل الأقسام معروضة":"عرض كل الأقسام";};
 const apply=()=>focus(items.some(([id])=>id===hash())||hash()==="recoveryLog"?hash():"positions");
 all.addEventListener("click",()=>focus("all"));window.addEventListener("hashchange",apply);
 tabs.addEventListener("click",e=>{const a=e.target.closest("a");if(a)focus(a.hash.slice(1));});apply();
}}
installTableContainment();tick();setInterval(tick,30000);refreshActive();window.addEventListener("hashchange",refreshActive);
if(hash())requestAnimationFrame(()=>document.getElementById(hash())?.scrollIntoView({block:"start"}));
})();