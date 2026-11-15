/* Owner-only navigation improvements; no change to authorization or APIs. */
(()=>{
 "use strict";
 const view=document.getElementById("adminView");
 if(!view)return;
 const sidebar=view.querySelector(".az-admin-sidebar");
 const main=view.querySelector(".az-admin-content");
 if(!sidebar||!main)return;
 sidebar.id="az-owner-menu";
 const paths=[
  ["adminOverview","نظرة عامة"],["adminClients","العملاء"],["ownerPairPanel","ربط MT5"],
  ["councilPanel","مجلس الذكاء"],["actionPanel","إعدادات المحرك"],
  ["learningPanel","التعلم"],["versionsPanel","الاستراتيجيات والإصدارات"]
 ].filter(([id])=>document.getElementById(id));
 const toolbar=document.createElement("nav");toolbar.className="az-owner-shortcuts";toolbar.setAttribute("aria-label","انتقال سريع في بوابة المالك");
 const text=document.createElement("b");text.textContent="اذهب مباشرة إلى:";
 toolbar.appendChild(text);
 for(const [id,label] of paths){
  const a=document.createElement("a");a.href="#"+id;a.textContent=label;toolbar.appendChild(a);
 }
 const welcome=main.querySelector(".welcome");if(welcome)welcome.insertAdjacentElement("afterend",toolbar);else main.prepend(toolbar);
 const bar=document.createElement("div");bar.className="az-owner-mobile-bar";
 const button=document.createElement("button");button.type="button";button.textContent="☰  قائمة المالك";button.className="az-owner-menu-trigger";
 button.setAttribute("aria-controls","az-owner-menu");button.setAttribute("aria-expanded","false");
 bar.appendChild(button);view.insertBefore(bar,sidebar);
 const overlay=document.createElement("div");overlay.className="az-owner-overlay";view.appendChild(overlay);
 const close=()=>{view.classList.remove("az-owner-menu-open");button.setAttribute("aria-expanded","false");document.body.classList.remove("az-owner-nav-lock")};
 const open=()=>{view.classList.add("az-owner-menu-open");button.setAttribute("aria-expanded","true");document.body.classList.add("az-owner-nav-lock")};
 button.addEventListener("click",()=>view.classList.contains("az-owner-menu-open")?close():open());
 overlay.addEventListener("click",close);
 sidebar.addEventListener("click",e=>{if(e.target.closest("a"))close()});
 document.addEventListener("keydown",e=>{if(e.key==="Escape")close()});
 window.addEventListener("resize",()=>{if(innerWidth>900)close()},{passive:true});
})();