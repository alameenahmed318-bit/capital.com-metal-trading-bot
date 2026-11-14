/* AL-ZOOL Studio unified navigation: same sidebar on every trading/research page. */
(()=>{
  const shell=document.querySelector(".shell");
  const sidebar=document.querySelector(".sidebar");
  if(!shell||!sidebar)return;
  const current=location.pathname.split("/").filter(Boolean).pop()||"dashboard.html";
  const groups=[
    ["القيادة",[["dashboard.html","⌂","لوحة القيادة"],["opportunities.html","◎","الفرص والإشارات"]]],
    ["الذكاء والتحليل",[["ai-analysis.html","✧","مجلس الذكاء"],["learning-lab.html","◈","التعلّم والذاكرة"],["backtest-lab.html","⌁","الاختبارات التاريخية"]]],
    ["التداول",[["trade-management.html","⇄","الصفقات والحماية"],["trade-history.html","▤","السجل والتقارير"]]],
    ["الإدارة",[["","⚙","المالك والعملاء"]]]
  ];
  const status=sidebar.querySelector("#serviceState");
  const statusText=status?.textContent||"جارٍ التحقق من الخدمة";
  const brand=document.createElement("a");
  brand.href="/dashboard.html";brand.className="brand";
  brand.innerHTML='<span class="brand-mark" aria-hidden="true">🇸🇩</span><span><b>الزول · AL-ZOOL</b><small>SMART TRADING CONTROL</small></span>';
  sidebar.replaceChildren(brand);
  for(const [heading,items] of groups){
    const title=document.createElement("div");title.className="side-label";title.textContent=heading;
    sidebar.appendChild(title);
    const group=document.createElement("div");group.className="nav-group";
    for(const [page,glyph,label] of items){
      const a=document.createElement("a");a.className="nav"+(current===page?" active":"");
      a.href="/"+page;if(current===page)a.setAttribute("aria-current","page");
      const icon=document.createElement("span");icon.textContent=glyph;icon.setAttribute("aria-hidden","true");
      const text=document.createElement("span");text.className="nav-label";text.textContent=label;
      a.append(icon,text);group.appendChild(a);
    }
    sidebar.appendChild(group);
  }
  const bottom=document.createElement("div");bottom.className="side-bottom";
  bottom.innerHTML='<span class="live-dot" aria-hidden="true"></span><span>AL-ZOOL</span><small id="serviceState"></small>';
  bottom.querySelector("small").textContent=statusText;sidebar.appendChild(bottom);
  const key="alzool-ui-sidebar-collapsed",mobile=()=>window.matchMedia("(max-width:900px)").matches;
  let collapsed=false;
  try{collapsed=localStorage.getItem(key)==="1"}catch{}
  const button=document.createElement("button");button.type="button";button.className="ui-sidebar-toggle";
  button.setAttribute("aria-label","طي أو توسيع القائمة");
  button.innerHTML='<span class="toggle-glyph" aria-hidden="true">☰</span><span class="toggle-label">طي القائمة</span>';
  brand.insertAdjacentElement("afterend",button);
  const apply=()=>{
    const compact=collapsed&&!mobile();
    shell.classList.toggle("ui-collapsed",compact);
    button.querySelector(".toggle-label").textContent=compact?"توسيع القائمة":"طي القائمة";
    button.querySelector(".toggle-glyph").textContent=compact?"☷":"☰";
    button.setAttribute("aria-expanded",String(!compact))
  };
  button.addEventListener("click",()=>{collapsed=!collapsed;
    try{localStorage.setItem(key,collapsed?"1":"0")}catch{}apply()});
  window.addEventListener("resize",apply,{passive:true});apply();
})();