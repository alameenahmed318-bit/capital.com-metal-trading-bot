/* AL-ZOOL Strategy Engine Core
 * Pure analysis module. No MT5 connection, order execution, or license access.
 * Strategy contract: { id, name, family, evaluate(context) => { signal, confidence, reasons } }
 */
(function (root) {
  "use strict";

  const SIGNALS = Object.freeze({ BUY: "BUY", SELL: "SELL", WAIT: "WAIT" });

  function ema(values, period) {
    if (!Array.isArray(values) || values.length < period || period < 1) return null;
    let value = values.slice(0, period).reduce((sum, item) => sum + item, 0) / period;
    const alpha = 2 / (period + 1);
    for (let i = period; i < values.length; i++) value = values[i] * alpha + value * (1 - alpha);
    return value;
  }

  function indicators(candles) {
    const rows = candles.slice(-100);
    const closes = rows.map(row => Number(row.close));
    const fast = ema(closes, 20);
    const slow = ema(closes, 50);
    const changes = closes.slice(1).map((value, index) => value - closes[index]);
    const gains = changes.slice(-14).map(value => Math.max(0, value));
    const losses = changes.slice(-14).map(value => Math.max(0, -value));
    const avgGain = gains.length ? gains.reduce((a, b) => a + b, 0) / gains.length : 0;
    const avgLoss = losses.length ? losses.reduce((a, b) => a + b, 0) / losses.length : 0;
    const rsi = changes.length >= 14 ? (avgLoss === 0 ? 100 : 100 - 100 / (1 + avgGain / avgLoss)) : null;
    const ranges = rows.slice(1).map((row, index) =>
      Math.max(Number(row.high) - Number(row.low),
        Math.abs(Number(row.high) - Number(rows[index].close)),
        Math.abs(Number(row.low) - Number(rows[index].close))));
    const atr = ranges.length >= 14 ? ranges.slice(-14).reduce((a, b) => a + b, 0) / 14 : null;
    const prior = rows.slice(-21, -1);
    return {
      candles: rows, close: closes.at(-1), e20: fast, e50: slow, rsi, atr,
      high20: prior.length ? Math.max(...prior.map(row => Number(row.high))) : null,
      low20: prior.length ? Math.min(...prior.map(row => Number(row.low))) : null,
      high30: Math.max(...rows.slice(-30).map(row => Number(row.high))),
      low30: Math.min(...rows.slice(-30).map(row => Number(row.low))),
      last: rows.at(-1), prev5: ema(closes.slice(0, -5), 20)
    };
  }

  const strategies = [
    {
      id: "trend-ema-20-50", name: "اتجاه المتوسطات", family: "trend",
      evaluate: c => ({ signal: c.e20 > c.e50 ? SIGNALS.BUY : c.e20 < c.e50 ? SIGNALS.SELL : SIGNALS.WAIT,
        confidence: c.e20 === c.e50 ? 0 : 70, reasons: ["EMA20 " + c.e20.toFixed(3) + " مقابل EMA50 " + c.e50.toFixed(3)] })
    },
    {
      id: "price-vs-ema20", name: "موقع السعر", family: "trend",
      evaluate: c => ({ signal: c.close > c.e20 ? SIGNALS.BUY : c.close < c.e20 ? SIGNALS.SELL : SIGNALS.WAIT,
        confidence: c.close === c.e20 ? 0 : 60, reasons: ["الإغلاق " + c.close.toFixed(3) + " مقابل EMA20"] })
    },
    {
      id: "rsi-momentum", name: "الزخم RSI", family: "momentum",
      evaluate: c => ({ signal: c.rsi >= 55 && c.rsi < 75 ? SIGNALS.BUY : c.rsi <= 45 && c.rsi > 25 ? SIGNALS.SELL : SIGNALS.WAIT,
        confidence: c.rsi >= 55 && c.rsi < 75 || c.rsi <= 45 && c.rsi > 25 ? 65 : 0, reasons: ["RSI14 = " + c.rsi.toFixed(1)] })
    },
    {
      id: "breakout-20", name: "اختراق 20 شمعة", family: "breakout",
      evaluate: c => ({ signal: c.close > c.high20 ? SIGNALS.BUY : c.close < c.low20 ? SIGNALS.SELL : SIGNALS.WAIT,
        confidence: c.close > c.high20 || c.close < c.low20 ? 75 : 0,
        reasons: ["النطاق السابق " + c.low20.toFixed(3) + " – " + c.high20.toFixed(3)] })
    },
    {
      id: "three-candle-momentum", name: "زخم آخر 3 شموع", family: "candles",
      evaluate: c => {
        const recent = c.candles.slice(-3);
        const move = Number(recent.at(-1).close) - Number(recent[0].open);
        return { signal: move > 0 ? SIGNALS.BUY : move < 0 ? SIGNALS.SELL : SIGNALS.WAIT,
          confidence: move === 0 ? 0 : 55, reasons: ["التغير " + move.toFixed(3)] };
      }
    }
  ];


  // Batch 2: 15 additional strategies, evaluated independently.
  const extraStrategies = [
    {id:"ema-9-21",name:"تقاطع EMA 9/21",family:"trend",evaluate:c=>{const v=c.candles.map(x=>Number(x.close));const a=ema(v,9),b=ema(v,21);return {signal:a>b?SIGNALS.BUY:a<b?SIGNALS.SELL:SIGNALS.WAIT,confidence:a===b?0:68,reasons:["EMA9 مقابل EMA21"]};}},
    {id:"ema50-slope",name:"ميل EMA50",family:"trend",evaluate:c=>{const v=c.candles.map(x=>Number(x.close));const a=ema(v,50),b=ema(v.slice(0,-5),50);return {signal:a>b?SIGNALS.BUY:a<b?SIGNALS.SELL:SIGNALS.WAIT,confidence:a===b?0:62,reasons:["ميل EMA50 خلال 5 شموع"]};}},
    {id:"ema-pullback",name:"ارتداد السعر من EMA20",family:"trend",evaluate:c=>{const p=c.candles.at(-2),n=c.last;const buy=Number(p.low)<=c.e20&&c.close>c.e20&&c.close>Number(n.open),sell=Number(p.high)>=c.e20&&c.close<c.e20&&c.close<Number(n.open);return {signal:buy?SIGNALS.BUY:sell?SIGNALS.SELL:SIGNALS.WAIT,confidence:buy||sell?66:0,reasons:["اختبار EMA20 ثم إغلاق باتجاه الارتداد"]};}},
    {id:"rsi-extreme-reversal",name:"انعكاس تشبع RSI",family:"reversal",evaluate:c=>{const buy=c.rsi<=30,sell=c.rsi>=70;return {signal:buy?SIGNALS.BUY:sell?SIGNALS.SELL:SIGNALS.WAIT,confidence:buy||sell?64:0,reasons:["RSI14 = "+c.rsi.toFixed(1)]};}},
    {id:"rsi-midline",name:"عبور مستوى RSI 50",family:"momentum",evaluate:c=>{const v=c.candles.map(x=>Number(x.close));const calc=a=>{const d=a.slice(1).map((x,i)=>x-a[i]),g=d.slice(-14).reduce((t,x)=>t+Math.max(0,x),0)/14,l=d.slice(-14).reduce((t,x)=>t+Math.max(0,-x),0)/14;return l===0?100:100-100/(1+g/l)};const p=calc(v.slice(0,-1));return {signal:p<=50&&c.rsi>50?SIGNALS.BUY:p>=50&&c.rsi<50?SIGNALS.SELL:SIGNALS.WAIT,confidence:70,reasons:["تغير RSI عبر مستوى 50"]};}},
    {id:"bollinger-reentry",name:"عودة نطاق بولنجر",family:"reversal",evaluate:c=>{const v=c.candles.map(x=>Number(x.close)).slice(-20),m=v.reduce((a,b)=>a+b,0)/v.length,sd=Math.sqrt(v.reduce((a,b)=>a+(b-m)**2,0)/v.length),p=Number(c.candles.at(-2).close),buy=p<m-2*sd&&c.close>m-2*sd,sell=p>m+2*sd&&c.close<m+2*sd;return {signal:buy?SIGNALS.BUY:sell?SIGNALS.SELL:SIGNALS.WAIT,confidence:buy||sell?67:0,reasons:["عودة الإغلاق داخل نطاق بولنجر 20/2"]};}},
    {id:"range-breakout-30",name:"اختراق نطاق 30 شمعة",family:"breakout",evaluate:c=>{const p=c.candles.slice(-31,-1),h=Math.max(...p.map(x=>Number(x.high))),l=Math.min(...p.map(x=>Number(x.low)));return {signal:c.close>h?SIGNALS.BUY:c.close<l?SIGNALS.SELL:SIGNALS.WAIT,confidence:c.close>h||c.close<l?76:0,reasons:["نطاق 30 شمعة: "+l.toFixed(3)+" – "+h.toFixed(3)]};}},
    {id:"atr-expansion",name:"اندفاع سعري مقارنة ATR",family:"momentum",evaluate:c=>{const body=c.close-Number(c.last.open),ok=c.atr&&Math.abs(body)>c.atr*.8;return {signal:ok?(body>0?SIGNALS.BUY:SIGNALS.SELL):SIGNALS.WAIT,confidence:ok?68:0,reasons:["جسم الشمعة مقارنة ATR"]};}},
    {id:"engulfing",name:"الابتلاع الشرائي/البيعي",family:"candles",evaluate:c=>{const a=c.candles.at(-2),b=c.last,ao=Number(a.open),ac=Number(a.close),bo=Number(b.open),bc=Number(b.close),buy=ac<ao&&bc>bo&&bo<=ac&&bc>=ao,sell=ac>ao&&bc<bo&&bo>=ac&&bc<=ao;return {signal:buy?SIGNALS.BUY:sell?SIGNALS.SELL:SIGNALS.WAIT,confidence:buy||sell?72:0,reasons:["نموذج الابتلاع على آخر شمعتين"]};}},
    {id:"pinbar-rejection",name:"شمعة رفض سعري",family:"candles",evaluate:c=>{const n=c.last,o=Number(n.open),cl=Number(n.close),h=Number(n.high),l=Number(n.low),b=Math.max(Math.abs(cl-o),1e-8),lo=Math.min(o,cl)-l,up=h-Math.max(o,cl),buy=lo>=b*2&&up<=b,sell=up>=b*2&&lo<=b;return {signal:buy?SIGNALS.BUY:sell?SIGNALS.SELL:SIGNALS.WAIT,confidence:buy||sell?65:0,reasons:["تحليل ظلال وجسم الشمعة"]};}},
    {id:"inside-bar-break",name:"اختراق شمعة داخلية",family:"breakout",evaluate:c=>{const a=c.candles.at(-2),p=c.candles.at(-3),inside=Number(a.high)<Number(p.high)&&Number(a.low)>Number(p.low),buy=inside&&c.close>Number(a.high),sell=inside&&c.close<Number(a.low);return {signal:buy?SIGNALS.BUY:sell?SIGNALS.SELL:SIGNALS.WAIT,confidence:buy||sell?70:0,reasons:["تأكيد كسر نطاق الشمعة الداخلية"]};}},
    {id:"support-bounce",name:"ارتداد من الدعم القريب",family:"support_resistance",evaluate:c=>{const near=Math.abs(Number(c.last.low)-c.low30)<=(c.atr||0)*.2,up=c.close>Number(c.last.open);return {signal:near&&up?SIGNALS.BUY:SIGNALS.WAIT,confidence:near&&up?61:0,reasons:["تفاعل السعر مع قاع 30 شمعة"]};}},
    {id:"resistance-rejection",name:"رفض المقاومة القريبة",family:"support_resistance",evaluate:c=>{const near=Math.abs(Number(c.last.high)-c.high30)<=(c.atr||0)*.2,down=c.close<Number(c.last.open);return {signal:near&&down?SIGNALS.SELL:SIGNALS.WAIT,confidence:near&&down?61:0,reasons:["تفاعل السعر مع قمة 30 شمعة"]};}},
    {id:"five-candle-roc",name:"زخم التغير 5 شموع",family:"momentum",evaluate:c=>{const v=c.candles.map(x=>Number(x.close)),d=v.at(-1)-v.at(-6);return {signal:d>0?SIGNALS.BUY:d<0?SIGNALS.SELL:SIGNALS.WAIT,confidence:d===0?0:58,reasons:["التغير خلال 5 شموع: "+d.toFixed(3)]};}},
    {id:"macd-alignment",name:"توافق MACD",family:"momentum",evaluate:c=>{const v=c.candles.map(x=>Number(x.close)),line=ema(v,12)-ema(v,26),hist=ema(v.slice(-40).map((_,i)=>{const q=v.slice(0,v.length-39+i);return ema(q,12)-ema(q,26)}),9),buy=line>0&&line>hist,sell=line<0&&line<hist;return {signal:buy?SIGNALS.BUY:sell?SIGNALS.SELL:SIGNALS.WAIT,confidence:buy||sell?66:0,reasons:["مقارنة خط MACD بخط الإشارة"]};}},
    {id:"candle-body-pressure",name:"قوة جسم الشمعة",family:"candles",evaluate:c=>{const n=c.last,body=c.close-Number(n.open),range=Math.max(Number(n.high)-Number(n.low),1e-8),ratio=Math.abs(body)/range,ok=ratio>=.7;return {signal:ok?(body>0?SIGNALS.BUY:SIGNALS.SELL):SIGNALS.WAIT,confidence:ok?60:0,reasons:["قوة جسم الشمعة "+(ratio*100).toFixed(0)+"% من مداها"]};}}
  ];


  // Batch 3: 20 more strategy models (40 total).
  const batchThreeStrategies = [
    {id:"sma-20-50",name:"تقاطع SMA 20/50",family:"trend",evaluate:c=>{const v=c.candles.map(x=>Number(x.close)),a=v.slice(-20).reduce((x,y)=>x+y,0)/20,b=v.slice(-50).reduce((x,y)=>x+y,0)/50;return {signal:a>b?SIGNALS.BUY:a<b?SIGNALS.SELL:SIGNALS.WAIT,confidence:65,reasons:["مقارنة المتوسط البسيط 20/50"]};}},
    {id:"ema-5-13",name:"تقاطع EMA 5/13",family:"trend",evaluate:c=>{const v=c.candles.map(x=>Number(x.close)),a=ema(v,5),b=ema(v,13);return {signal:a>b?SIGNALS.BUY:a<b?SIGNALS.SELL:SIGNALS.WAIT,confidence:64,reasons:["مقارنة EMA5 وEMA13"]};}},
    {id:"ema-50-100",name:"اتجاه EMA 50/100",family:"trend",evaluate:c=>{const v=c.candles.map(x=>Number(x.close)),a=ema(v,50),b=ema(v,100);return {signal:a>b?SIGNALS.BUY:a<b?SIGNALS.SELL:SIGNALS.WAIT,confidence:68,reasons:["اتجاه المتوسطين EMA50/EMA100"]};}},
    {id:"donchian-10",name:"اختراق قناة دونشيان 10",family:"breakout",evaluate:c=>{const p=c.candles.slice(-11,-1),h=Math.max(...p.map(x=>Number(x.high))),l=Math.min(...p.map(x=>Number(x.low)));return {signal:c.close>h?SIGNALS.BUY:c.close<l?SIGNALS.SELL:SIGNALS.WAIT,confidence:70,reasons:["حدود قناة دونشيان 10"]};}},
    {id:"donchian-50",name:"اختراق قناة دونشيان 50",family:"breakout",evaluate:c=>{const p=c.candles.slice(-51,-1),h=Math.max(...p.map(x=>Number(x.high))),l=Math.min(...p.map(x=>Number(x.low)));return {signal:c.close>h?SIGNALS.BUY:c.close<l?SIGNALS.SELL:SIGNALS.WAIT,confidence:78,reasons:["حدود قناة دونشيان 50"]};}},
    {id:"stochastic-14",name:"مؤشر Stochastic 14",family:"momentum",evaluate:c=>{const p=c.candles.slice(-14),h=Math.max(...p.map(x=>Number(x.high))),l=Math.min(...p.map(x=>Number(x.low))),k=h===l?50:(c.close-l)/(h-l)*100;return {signal:k<20?SIGNALS.BUY:k>80?SIGNALS.SELL:SIGNALS.WAIT,confidence:k<20||k>80?63:0,reasons:["Stochastic %K = "+k.toFixed(1)]};}},
    {id:"cci-20",name:"مؤشر CCI 20",family:"momentum",evaluate:c=>{const p=c.candles.slice(-20).map(x=>(Number(x.high)+Number(x.low)+Number(x.close))/3),m=p.reduce((a,b)=>a+b,0)/p.length,md=p.reduce((a,b)=>a+Math.abs(b-m),0)/p.length,cci=md?((p.at(-1)-m)/(.015*md)):0;return {signal:cci>100?SIGNALS.BUY:cci< -100?SIGNALS.SELL:SIGNALS.WAIT,confidence:Math.abs(cci)>100?65:0,reasons:["CCI20 = "+cci.toFixed(1)]};}},
    {id:"roc-10",name:"معدل التغير ROC 10",family:"momentum",evaluate:c=>{const v=c.candles.map(x=>Number(x.close)),base=v.at(-11),roc=base?((c.close-base)/base)*100:0;return {signal:roc>0?SIGNALS.BUY:roc<0?SIGNALS.SELL:SIGNALS.WAIT,confidence:Math.min(70,Math.abs(roc)*20),reasons:["ROC10 = "+roc.toFixed(3)+"%"]};}},
    {id:"momentum-10",name:"مؤشر Momentum 10",family:"momentum",evaluate:c=>{const v=c.candles.map(x=>Number(x.close)),d=c.close-v.at(-11);return {signal:d>0?SIGNALS.BUY:d<0?SIGNALS.SELL:SIGNALS.WAIT,confidence:d===0?0:60,reasons:["فرق السعر خلال 10 شموع: "+d.toFixed(3)]};}},
    {id:"adx-directional",name:"قوة الاتجاه ADX/DI",family:"trend",evaluate:c=>{const p=c.candles.slice(-15);let plus=0,minus=0,tr=0;for(let i=1;i<p.length;i++){const up=Number(p[i].high)-Number(p[i-1].high),dn=Number(p[i-1].low)-Number(p[i].low);plus+=up>dn&&up>0?up:0;minus+=dn>up&&dn>0?dn:0;tr+=Math.max(Number(p[i].high)-Number(p[i].low),Math.abs(Number(p[i].high)-Number(p[i-1].close)),Math.abs(Number(p[i].low)-Number(p[i-1].close)));}const dip=tr?plus/tr*100:0,dim=tr?minus/tr*100:0,dx=(dip+dim)?Math.abs(dip-dim)/(dip+dim)*100:0;return {signal:dx>20?(dip>dim?SIGNALS.BUY:SIGNALS.SELL):SIGNALS.WAIT,confidence:dx>20?Math.min(75,dx):0,reasons:["DI+ "+dip.toFixed(1)+" / DI- "+dim.toFixed(1)+"؛ قوة الاتجاه "+dx.toFixed(1)]};}},
    {id:"keltner-break",name:"اختراق قناة كيلتنر",family:"breakout",evaluate:c=>{const v=c.candles.map(x=>Number(x.close)),mid=ema(v,20),upper=mid+1.5*(c.atr||0),lower=mid-1.5*(c.atr||0);return {signal:c.close>upper?SIGNALS.BUY:c.close<lower?SIGNALS.SELL:SIGNALS.WAIT,confidence:c.close>upper||c.close<lower?68:0,reasons:["حدود قناة كيلتنر 20/1.5"]};}},
    {id:"vwap-20",name:"السعر مقابل VWAP 20",family:"trend",evaluate:c=>{const p=c.candles.slice(-20);let pv=0,vol=0;for(const x of p){const v=Math.max(1,Number(x.tick_volume??x.volume??1)),tp=(Number(x.high)+Number(x.low)+Number(x.close))/3;pv+=tp*v;vol+=v;}const w=pv/vol;return {signal:c.close>w?SIGNALS.BUY:c.close<w?SIGNALS.SELL:SIGNALS.WAIT,confidence:60,reasons:["السعر مقابل VWAP تقريبي 20 شمعة"]};}},
    {id:"pivot-break",name:"اختراق نقطة الارتكاز",family:"support_resistance",evaluate:c=>{const p=c.candles.at(-2),pivot=(Number(p.high)+Number(p.low)+Number(p.close))/3,r=2*pivot-Number(p.low),s=2*pivot-Number(p.high);return {signal:c.close>r?SIGNALS.BUY:c.close<s?SIGNALS.SELL:SIGNALS.WAIT,confidence:c.close>r||c.close<s?66:0,reasons:["Pivot "+pivot.toFixed(3)+"؛ R1 "+r.toFixed(3)+"؛ S1 "+s.toFixed(3)]};}},
    {id:"fibonacci-reclaim",name:"استرداد فيبوناتشي 38.2%",family:"reversal",evaluate:c=>{const p=c.candles.slice(-50),h=Math.max(...p.map(x=>Number(x.high))),l=Math.min(...p.map(x=>Number(x.low))),level=l+(h-l)*.382,prev=Number(c.candles.at(-2).close),buy=prev<level&&c.close>level,sell=prev>level&&c.close<level;return {signal:buy?SIGNALS.BUY:sell?SIGNALS.SELL:SIGNALS.WAIT,confidence:buy||sell?64:0,reasons:["مستوى فيبوناتشي 38.2 عند "+level.toFixed(3)]};}},
    {id:"swing-fractal-break",name:"كسر قمة/قاع محوري",family:"breakout",evaluate:c=>{const p=c.candles.slice(-12,-2),h=Math.max(...p.map(x=>Number(x.high))),l=Math.min(...p.map(x=>Number(x.low)));return {signal:c.close>h?SIGNALS.BUY:c.close<l?SIGNALS.SELL:SIGNALS.WAIT,confidence:c.close>h||c.close<l?67:0,reasons:["كسر آخر نطاق محوري محلي"]};}},
    {id:"hammer-reversal",name:"نموذج المطرقة",family:"candles",evaluate:c=>{const n=c.last,o=Number(n.open),cl=Number(n.close),h=Number(n.high),l=Number(n.low),b=Math.max(Math.abs(cl-o),1e-8),lower=Math.min(o,cl)-l,upper=h-Math.max(o,cl),ok=lower>=2*b&&upper<=b&&cl>=o;return {signal:ok?SIGNALS.BUY:SIGNALS.WAIT,confidence:ok?67:0,reasons:["فحص المطرقة بذيل سفلي طويل"]};}},
    {id:"shooting-star",name:"نموذج الشهاب",family:"candles",evaluate:c=>{const n=c.last,o=Number(n.open),cl=Number(n.close),h=Number(n.high),l=Number(n.low),b=Math.max(Math.abs(cl-o),1e-8),upper=h-Math.max(o,cl),lower=Math.min(o,cl)-l,ok=upper>=2*b&&lower<=b&&cl<=o;return {signal:ok?SIGNALS.SELL:SIGNALS.WAIT,confidence:ok?67:0,reasons:["فحص الشهاب بذيل علوي طويل"]};}},
    {id:"morning-star",name:"نموذج نجمة الصباح",family:"candles",evaluate:c=>{const a=c.candles.at(-3),b=c.candles.at(-2),d=c.last,ao=Number(a.open),ac=Number(a.close),bo=Number(b.open),bc=Number(b.close),dc=Number(d.close),mid=(ao+ac)/2,ok=ac<ao&&Math.abs(bc-bo)<Math.abs(ac-ao)*.5&&dc>mid;return {signal:ok?SIGNALS.BUY:SIGNALS.WAIT,confidence:ok?70:0,reasons:["نموذج انعكاس ثلاثي الشموع صاعد"]};}},
    {id:"evening-star",name:"نموذج نجمة المساء",family:"candles",evaluate:c=>{const a=c.candles.at(-3),b=c.candles.at(-2),d=c.last,ao=Number(a.open),ac=Number(a.close),bo=Number(b.open),bc=Number(b.close),dc=Number(d.close),mid=(ao+ac)/2,ok=ac>ao&&Math.abs(bc-bo)<Math.abs(ac-ao)*.5&&dc<mid;return {signal:ok?SIGNALS.SELL:SIGNALS.WAIT,confidence:ok?70:0,reasons:["نموذج انعكاس ثلاثي الشموع هابط"]};}},
    {id:"doji-indecision",name:"شمعة دوجي وتردد السوق",family:"candles",evaluate:c=>{const n=c.last,o=Number(n.open),cl=Number(n.close),h=Number(n.high),l=Number(n.low),range=Math.max(h-l,1e-8),ratio=Math.abs(cl-o)/range;return {signal:SIGNALS.WAIT,confidence:ratio<.1?35:0,reasons:[ratio<.1?"شمعة دوجي: السوق متردد، لا إشارة اتجاهية":"لا يوجد دوجي واضح"]};}}
  ];

  const registry = new Map();
  function register(strategy) {
    if (!strategy || !strategy.id || !strategy.name || !strategy.family || typeof strategy.evaluate !== "function")
      throw new TypeError("Invalid strategy contract");
    if (registry.has(strategy.id)) throw new Error("Duplicate strategy id: " + strategy.id);
    registry.set(strategy.id, Object.freeze(strategy));
    return strategy.id;
  }
  strategies.forEach(register);
  extraStrategies.forEach(register);
  batchThreeStrategies.forEach(register);

  function evaluate(market) {
    const candles = Array.isArray(market && market.candles) ? market.candles : [];
    const z = indicators(candles);
    const tick = market && market.tick || {};
    if (z.candles.length < 50 || z.rsi === null || !Number(tick.bid) || !Number(tick.ask)) {
      return { signal: SIGNALS.WAIT, confidence: 0, z, buy: 0, sell: 0,
        votes: [{ id: "data-readiness", name: "كفاية البيانات", family: "system", vote: SIGNALS.WAIT,
          why: "نحتاج 50 شمعة مغلقة على الأقل وسعر Bid/Ask صالحاً." }],
        reasons: ["البيانات غير كافية لتأكيد اتجاه موثوق."] };
    }
    const context = Object.freeze({ ...z, market });
    const votes = Array.from(registry.values()).map(strategy => {
      try {
        const result = strategy.evaluate(context);
        return { id: strategy.id, name: strategy.name, family: strategy.family,
          vote: result.signal, why: (result.reasons || []).join("؛ "), confidence: result.confidence || 0 };
      } catch (error) {
        return { id: strategy.id, name: strategy.name, family: strategy.family,
          vote: SIGNALS.WAIT, why: "تعذر تقييم هذه الاستراتيجية: " + String(error && error.message || error), confidence: 0 };
      }
    });
    const buy = votes.filter(vote => vote.vote === SIGNALS.BUY).length;
    const sell = votes.filter(vote => vote.vote === SIGNALS.SELL).length;
    const quorum = Math.max(3, Math.ceil(votes.length * 0.35));
    const signal = buy >= quorum && buy - sell >= 2 ? SIGNALS.BUY :
      sell >= quorum && sell - buy >= 2 ? SIGNALS.SELL : SIGNALS.WAIT;
    let confidence = Math.round(Math.max(buy, sell) / votes.length * 100);
    if (signal === SIGNALS.WAIT) confidence = Math.round(Math.max(buy, sell) / votes.length * 70);
    return {
      signal, confidence, z, votes, buy, sell,
      reasons: ["تصويت " + buy + " من " + votes.length + " لصالح الشراء و" + sell + " لصالح البيع.",
        "EMA20: " + z.e20.toFixed(3) + " | EMA50: " + z.e50.toFixed(3) + " | RSI14: " + z.rsi.toFixed(1),
        "ATR14: " + (z.atr === null ? "غير متاح" : z.atr.toFixed(3)) +
          " | الدعم المرجعي: " + z.low30.toFixed(3) + " | المقاومة المرجعية: " + z.high30.toFixed(3)]
    };
  }

  root.ALZOOLStrategyEngine = Object.freeze({
    evaluate, register, list: () => Array.from(registry.values()).map(({ id, name, family }) => ({ id, name, family })),
    families: () => Array.from(new Set(Array.from(registry.values(), item => item.family))),
    get size() { return registry.size; }
  });
})(window);
