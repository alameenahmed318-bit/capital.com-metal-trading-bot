"""Browser layout regression: real HTML/CSS and the real Shadow shell.
Only fake UI fixture rows are used; no account, network, broker or trade access."""
from pathlib import Path
from playwright.sync_api import sync_playwright
import json, os, threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

ROOT=Path(__file__).resolve().parents[1]
os.chdir(ROOT)
class Quiet(SimpleHTTPRequestHandler):
    def log_message(self,*args): pass
server=ThreadingHTTPServer(("127.0.0.1",0),Quiet)
threading.Thread(target=server.serve_forever,daemon=True).start()
origin=f"http://127.0.0.1:{server.server_port}"
pages=["dashboard.html","master-ai.html","model-arena.html","trade-management.html#positions","ai-analysis.html","ai-monitor.html","learning-lab.html","opportunities.html","backtest-lab.html","trade-history.html","bots-library.html","index.html?manage=1"]
widths=[390,768,1024,1280,1536,1920]
errors=[]
with sync_playwright() as p:
    browser=p.chromium.launch(headless=True,args=["--no-sandbox"])
    for route in pages:
      for width in widths:
        page=browser.new_page(viewport={"width":width,"height":900})
        # Exercise actual presentation assets while preventing every external,
        # authenticated, trading and AI script from accessing a live system.
        def intercept(request):
            path=request.request.url
            if not path.startswith(origin):
                request.abort()
            elif "/api/" in path or "/v1/" in path:
                request.fulfill(status=503,content_type="application/json",body='{"ready":false}')
            elif request.request.resource_type=="script" and "/ui-shell.js" not in path:
                request.fulfill(status=200,content_type="text/javascript",body="")
            else:
                request.continue_()
        page.route("**/*",intercept)
        page.add_init_script("""window.supabase={createClient(){return {auth:{getSession:async()=>({data:{session:null}})}}}}""")
        # Inline app scripts are removed for layout-only checks, except the shell is real.
        def document_route(rt):
            from urllib.parse import urlsplit
            name=urlsplit(rt.request.url).path.strip("/") or "index.html"
            import re
            html=(ROOT/name).read_text()
            html=re.sub(r'<script\\b(?![^>]*\\bsrc=)[^>]*>[\\s\\S]*?</script>',"",html,flags=re.I)
            rt.fulfill(status=200,content_type="text/html; charset=utf-8",body=html)
        page.route(lambda u: u.startswith(origin) and ".html" in u and not ".js" in u and not ".css" in u,document_route)
        page.goto(origin+"/"+route,wait_until="domcontentloaded")
        page.wait_for_selector(".ss-studio")
        if route.startswith("index"):
            page.evaluate("""()=>{for(const id of ['loginView','clientView','resetPasswordView'])document.getElementById(id)?.classList.add('hidden');document.getElementById('adminView')?.classList.remove('hidden')}""")
        # Populate real results containers with deliberately wide, long Arabic/English data.
        page.evaluate("""()=>{
          for(const id of ['strategyResults','comboResults','topStrategies','topCombinations','runsTable','details','events','tradesTable','tradeEventsTable']){
            const e=document.getElementById(id);if(!e||/^(TBODY|TABLE)$/.test(e.tagName))continue;
            e.innerHTML='<table style="min-width:1100px"><thead><tr>'+Array.from({length:10},(_,i)=>'<th>بيانات '+i+'</th>').join('')+'</tr></thead><tbody>'+Array.from({length:25},(_,i)=>'<tr>'+Array.from({length:10},(_,j)=>'<td>Long Strategy Name '+i+' / '+j+' — USD 12345.67</td>').join('')+'</tr>').join('')+'</tbody></table>';
          }
        }""")
        page.wait_for_timeout(100)
        data=page.evaluate("""()=>{
          const menu=document.querySelector('.ss-menu'),main=document.querySelector('.ss-workspace>main,.ss-portal #adminView>.az-admin-content');
          const bounds=e=>{const r=e.getBoundingClientRect();return {left:r.left,right:r.right,width:r.width,top:r.top,bottom:r.bottom}};
          if(!menu||!main)return {failure:'missing shell'};
          const m=bounds(menu),c=bounds(main),root=document.documentElement;
          const leaks=[];
          for(const el of main.querySelectorAll('.card,.panel,.lab-results,.op-columns,.lab-table-wrap,.op-table-wrap,.ss-table-scroll,.grid,.table-scroll,.table-wrap')){
            if(!el.getClientRects().length)continue;
            const r=bounds(el);if(r.left<c.left-2||r.right>c.right+2)leaks.push(el.className+' '+JSON.stringify(r));
          }
          return {width:innerWidth,scrollWidth:root.scrollWidth,menu:m,main:c,leaks,menuCount:menu.querySelectorAll(":scope>a").length,overlap:innerWidth>760?c.right>m.left+1:m.bottom>c.top+1};
        }""")
        if data.get("failure") or data.get("menuCount")!=6 or data.get("overlap") or data.get("leaks") or data.get("scrollWidth",0)>width+2:
            errors.append({"page":route,"viewport":width,**data})
            print("FAIL",json.dumps(errors[-1],ensure_ascii=False))
        else: print("PASS",route,width)
        page.close()
    browser.close()
server.shutdown()
assert not errors, json.dumps(errors,ensure_ascii=False,indent=2)
print(f"PASS: {len(pages)*len(widths)} real-page viewport checks")
