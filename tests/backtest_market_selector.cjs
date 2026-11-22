// Real inline backtest UI logic with isolated fake market archives.
const assert=require("node:assert/strict"),fs=require("node:fs"),vm=require("node:vm");
const source=fs.readFileSync("backtest-lab.html","utf8").match(/<script>\s*const \$=id[\s\S]*?<\/script>/)[0].replace(/^<script>|<\/script>$/g,"").replace(/\bboot\(\);\s*$/,"");
const els=new Map(),requests=[];
function el(id){if(!els.has(id))els.set(id,{value:"",disabled:false,hidden:false,textContent:"",innerHTML:"",style:{},addEventListener(){}});return els.get(id)}
const context={document:{getElementById:el},window:{ALZOOL_MARKET_VIEW:()=>"BTCUSD"},location:{search:"",href:"https://example.test/backtest-lab.html"},URL,URLSearchParams,AbortController,AbortSignal,DOMException,console,setTimeout,fetch:async path=>{
 requests.push(path);const gold=path.startsWith("/market/XAUUSD/");
 const symbol=gold?"XAUUSD":"BTCUSDm";
 return {ok:true,json:async()=>({available_symbols:[symbol],available_timeframes:["PERIOD_M1"],symbol,timeframe:"PERIOD_M1"})};
}};
vm.createContext(context);vm.runInContext(source,context);
(async()=>{
 assert.equal(el("backtestMarket").value,"BTCUSD");
 await vm.runInContext("loadHistoryMeta()",context);
 assert.equal(el("symbol").value,"BTCUSDm");assert.equal(el("runBtn").disabled,false);
 el("backtestMarket").value="XAUUSD";
 await vm.runInContext("loadHistoryMeta()",context);
 assert.equal(el("symbol").value,"XAUUSD");assert.equal(el("runBtn").disabled,false);
 assert.equal(requests.length,2);
 assert.ok(requests[0].startsWith("/market/BTCUSD/api/backtest/history?"));
 assert.ok(requests[1].startsWith("/market/XAUUSD/api/backtest/history?"));
 assert.ok(requests.every(p=>p.includes("meta=1")));
 assert.equal(vm.runInContext('marketPath("XAUUSD","/api/backtest/learning")',context),"/market/XAUUSD/api/backtest/learning");
 assert.ok(!requests.some(p=>p.includes("/select")));
 console.log("PASS: BTCUSDm to XAUUSD selection, isolated history and learning routing, no trading selection");
})().catch(e=>{console.error(e);process.exitCode=1});
