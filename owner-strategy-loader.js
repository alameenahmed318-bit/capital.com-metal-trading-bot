/* Owner-only loader. Never embed a long-lived secret in URLs or HTML. */
(function(){
  "use strict";
  window.ALZOOL_STRATEGY_READY=(async()=>{
    const cfgResp=await fetch("/api/config",{cache:"no-store"});
    if(!cfgResp.ok)throw Error("تعذر التحقق من الخادم");
    const cfg=await cfgResp.json();
    if(!window.supabase?.createClient)throw Error("خدمة تسجيل الدخول غير متاحة");
    const auth=window.supabase.createClient(cfg.supabase_url,cfg.supabase_anon_key);
    const {data,error}=await auth.auth.getSession();
    if(error||!data.session?.access_token)throw Error("سجل دخول المالك أولاً");
    const headers={Authorization:"Bearer "+data.session.access_token};
    const access=await fetch("/api/access/check",{headers,cache:"no-store"});
    if(!access.ok)throw Error("صلاحيات الدخول غير صالحة");
    const role=await access.json();
    if(role.role!=="owner")throw Error("الاستراتيجيات والتحليل مخصصة للمالك فقط");
    const source=await fetch("/strategy-engine.js",{headers,cache:"no-store"});
    if(!source.ok)throw Error("تم منع تحميل الاستراتيجيات");
    const js=await source.text();
    const element=document.createElement("script");
    element.textContent=js;
    document.head.appendChild(element);
    if(!window.ALZOOLStrategyEngine)throw Error("تعذر تحميل محرك الاستراتيجيات");
    return true;
  })();
  window.ALZOOL_STRATEGY_READY.catch(error=>{
    console.warn("Owner strategy module blocked:",error.message);
  });
})();
