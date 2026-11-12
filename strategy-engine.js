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


  // Batch 4: 20 established technical-analysis methods (40 -> 60).
  // Helpers use only the OHLCV candles actually supplied by MT5.
  function candleRangeMid(rows, period) {
    const p=rows.slice(-period);
    if(p.length<period)return null;
    return (Math.max(...p.map(x=>Number(x.high)))+Math.min(...p.map(x=>Number(x.low))))/2;
  }
  function trueRangeAt(rows,i) {
    const x=rows[i],prev=rows[i-1];
    return Math.max(Number(x.high)-Number(x.low),Math.abs(Number(x.high)-Number(prev.close)),Math.abs(Number(x.low)-Number(prev.close)));
  }
  function atrSeries(rows,period=10) {
    if(rows.length<period+1)return [];
    const out=[];let value=0;
    for(let i=1;i<=period;i++)value+=trueRangeAt(rows,i);
    value/=period;out[period]=value;
    for(let i=period+1;i<rows.length;i++){value=((value*(period-1))+trueRangeAt(rows,i))/period;out[i]=value;}
    return out;
  }
  function rsiSeries(values,period=14) {
    const out=[];
    for(let i=period;i<values.length;i++){
      let gain=0,loss=0;
      for(let j=i-period+1;j<=i;j++){const d=values[j]-values[j-1];gain+=Math.max(0,d);loss+=Math.max(0,-d);}
      gain/=period;loss/=period;out[i]=loss===0?100:100-100/(1+gain/loss);
    }
    return out;
  }
  function stochasticKD(rows,index,period=14) {
    const vals=[];
    for(let i=Math.max(0,index-period+1);i<=index;i++){
      const p=rows.slice(Math.max(0,i-2),i+1);
      const hh=Math.max(...p.map(x=>Number(x.high))),ll=Math.min(...p.map(x=>Number(x.low)));
      vals.push(hh===ll?50:(Number(rows[i].close)-ll)/(hh-ll)*100);
    }
    const k=vals.at(-1),prev=vals.at(-2);
    return {k,prev,d:vals.slice(-3).reduce((a,b)=>a+b,0)/Math.min(3,vals.length)};
  }
  function psarDirection(rows) {
    if(rows.length<5)return 0;
    let bull=Number(rows[1].close)>=Number(rows[0].close),af=.02,ep=bull?Number(rows[1].high):Number(rows[1].low),sar=bull?Number(rows[0].low):Number(rows[0].high);
    for(let i=2;i<rows.length;i++){
      const prev=rows[i-1],cur=rows[i];
      sar=sar+af*(ep-sar);
      if(bull){
        sar=Math.min(sar,Number(prev.low),Number(rows[i-2].low));
        if(Number(cur.low)<sar){bull=false;sar=ep;ep=Number(cur.low);af=.02;}
        else if(Number(cur.high)>ep){ep=Number(cur.high);af=Math.min(.2,af+.02);}
      }else{
        sar=Math.max(sar,Number(prev.high),Number(rows[i-2].high));
        if(Number(cur.high)>sar){bull=true;sar=ep;ep=Number(cur.high);af=.02;}
        else if(Number(cur.low)<ep){ep=Number(cur.low);af=Math.min(.2,af+.02);}
      }
    }
    return bull?1:-1;
  }
  function supertrendDirection(rows,period=10,mult=3) {
    const ats=atrSeries(rows,period);if(!ats.length)return 0;
    let trend=1,upper=0,lower=0;
    for(let i=period;i<rows.length;i++){
      const mid=(Number(rows[i].high)+Number(rows[i].low))/2,atr=ats[i];
      const basicUpper=mid+mult*atr,basicLower=mid-mult*atr;
      upper=(i===period||basicUpper<upper||Number(rows[i-1].close)>upper)?basicUpper:upper;
      lower=(i===period||basicLower>lower||Number(rows[i-1].close)<lower)?basicLower:lower;
      if(trend===1&&Number(rows[i].close)<lower)trend=-1;
      else if(trend===-1&&Number(rows[i].close)>upper)trend=1;
    }
    return trend;
  }
  function heikinAshi(rows) {
    const out=[];let prevOpen=(Number(rows[0].open)+Number(rows[0].close))/2,prevClose=(Number(rows[0].open)+Number(rows[0].high)+Number(rows[0].low)+Number(rows[0].close))/4;
    for(let i=0;i<rows.length;i++){
      const x=rows[i],close=(Number(x.open)+Number(x.high)+Number(x.low)+Number(x.close))/4;
      const open=i===0?prevOpen:(prevOpen+prevClose)/2;
      out.push({open,close,high:Math.max(Number(x.high),open,close),low:Math.min(Number(x.low),open,close)});
      prevOpen=open;prevClose=close;
    }
    return out;
  }
  const batchFourStrategies = [
    {id:"ichimoku-tenkan-kijun",name:"تقاطع إيشيموكو Tenkan/Kijun",family:"trend",evaluate:c=>{const r=c.candles,t=candleRangeMid(r,9),k=candleRangeMid(r,26),pt=candleRangeMid(r.slice(0,-1),9),pk=candleRangeMid(r.slice(0,-1),26),buy=pt<=pk&&t>k,sell=pt>=pk&&t<k;return {signal:buy?SIGNALS.BUY:sell?SIGNALS.SELL:SIGNALS.WAIT,confidence:buy||sell?72:0,reasons:["تقاطع خط التحويل 9 مع خط الأساس 26"]};}},
    {id:"ichimoku-cloud-breakout",name:"اختراق سحابة إيشيموكو",family:"breakout",evaluate:c=>{const r=c.candles,a=(candleRangeMid(r,9)+candleRangeMid(r,26))/2,b=candleRangeMid(r,52),top=Math.max(a,b),bottom=Math.min(a,b);return {signal:c.close>top?SIGNALS.BUY:c.close<bottom?SIGNALS.SELL:SIGNALS.WAIT,confidence:c.close>top||c.close<bottom?74:0,reasons:["إغلاق السعر خارج سحابة Kumo المحسوبة"]};}},
    {id:"ichimoku-price-kumo",name:"موقع السعر داخل/خارج Kumo",family:"trend",evaluate:c=>{const r=c.candles,a=(candleRangeMid(r,9)+candleRangeMid(r,26))/2,b=candleRangeMid(r,52),top=Math.max(a,b),bottom=Math.min(a,b);return {signal:c.close>top?SIGNALS.BUY:c.close<bottom?SIGNALS.SELL:SIGNALS.WAIT,confidence:c.close>top||c.close<bottom?62:0,reasons:["اتجاه السعر بالنسبة إلى حدود السحابة"]};}},
    {id:"ichimoku-chikou-confirm",name:"تأكيد خط Chikou",family:"trend",evaluate:c=>{const r=c.candles,ref=Number(r.at(-27).close),buy=c.close>ref&&c.close>c.e20,sell=c.close<ref&&c.close<c.e20;return {signal:buy?SIGNALS.BUY:sell?SIGNALS.SELL:SIGNALS.WAIT,confidence:buy||sell?63:0,reasons:["مقارنة الإغلاق الحالي بإغلاق 26 شمعة سابقة مع EMA20"]};}},
    {id:"supertrend-10-3",name:"مؤشر Supertrend 10/3",family:"trend",evaluate:c=>{const d=supertrendDirection(c.candles,10,3);return {signal:d>0?SIGNALS.BUY:d<0?SIGNALS.SELL:SIGNALS.WAIT,confidence:d?70:0,reasons:["اتجاه Supertrend باستخدام ATR10 ومضاعف 3"]};}},
    {id:"parabolic-sar",name:"Parabolic SAR",family:"trend",evaluate:c=>{const r=c.candles,dir=psarDirection(r),n=r.at(-1),prior=r.at(-2),bull=dir>0&&Number(n.close)>Number(n.open)&&Number(prior.close)>=Number(prior.open),bear=dir<0&&Number(n.close)<Number(n.open)&&Number(prior.close)<=Number(prior.open);return {signal:bull?SIGNALS.BUY:bear?SIGNALS.SELL:SIGNALS.WAIT,confidence:bull||bear?66:0,reasons:["اتجاه Parabolic SAR مع تأكيد آخر شمعتين"]};}},
    {id:"heikin-ashi-trend",name:"اتجاه شموع Heikin-Ashi",family:"candles",evaluate:c=>{const h=heikinAshi(c.candles).slice(-3),up=h.every(x=>x.close>x.open),dn=h.every(x=>x.close<x.open);return {signal:up?SIGNALS.BUY:dn?SIGNALS.SELL:SIGNALS.WAIT,confidence:up||dn?67:0,reasons:["ثلاث شموع Heikin-Ashi متتالية في اتجاه واحد"]};}},
    {id:"three-white-soldiers",name:"نموذج الجنود الثلاثة",family:"candles",evaluate:c=>{const r=c.candles.slice(-3),ok=r.every(x=>Number(x.close)>Number(x.open))&&r[1].close>r[0].close&&r[2].close>r[1].close&&r[1].open>r[0].open&&r[1].open<r[0].close&&r[2].open>r[1].open&&r[2].open<r[1].close;return {signal:ok?SIGNALS.BUY:SIGNALS.WAIT,confidence:ok?72:0,reasons:["ثلاث شموع صاعدة بأجسام متدرجة وافتتاح داخل الجسم السابق"]};}},
    {id:"three-black-crows",name:"نموذج الغربان الثلاثة",family:"candles",evaluate:c=>{const r=c.candles.slice(-3),ok=r.every(x=>Number(x.close)<Number(x.open))&&r[1].close<r[0].close&&r[2].close<r[1].close&&r[1].open<r[0].open&&r[1].open>r[0].close&&r[2].open<r[1].open&&r[2].open>r[1].close;return {signal:ok?SIGNALS.SELL:SIGNALS.WAIT,confidence:ok?72:0,reasons:["ثلاث شموع هابطة بأجسام متدرجة وافتتاح داخل الجسم السابق"]};}},
    {id:"tweezer-bottom",name:"قاع الملقط Tweezer Bottom",family:"reversal",evaluate:c=>{const a=c.candles.at(-2),b=c.last,tol=(c.atr||0)*.12,match=Math.abs(Number(a.low)-Number(b.low))<=tol&&Number(a.close)<Number(a.open)&&Number(b.close)>Number(b.open);return {signal:match?SIGNALS.BUY:SIGNALS.WAIT,confidence:match?66:0,reasons:["قاعان متقاربان مع انعكاس الشمعة الثانية صعوداً"]};}},
    {id:"tweezer-top",name:"قمة الملقط Tweezer Top",family:"reversal",evaluate:c=>{const a=c.candles.at(-2),b=c.last,tol=(c.atr||0)*.12,match=Math.abs(Number(a.high)-Number(b.high))<=tol&&Number(a.close)>Number(a.open)&&Number(b.close)<Number(b.open);return {signal:match?SIGNALS.SELL:SIGNALS.WAIT,confidence:match?66:0,reasons:["قمتان متقاربتان مع انعكاس الشمعة الثانية هبوطاً"]};}},
    {id:"bullish-harami",name:"نموذج Harami صاعد",family:"candles",evaluate:c=>{const a=c.candles.at(-2),b=c.last,ao=Number(a.open),ac=Number(a.close),bo=Number(b.open),bc=Number(b.close),ok=ac<ao&&bc>bo&&bo>ac&&bc<ao;return {signal:ok?SIGNALS.BUY:SIGNALS.WAIT,confidence:ok?65:0,reasons:["جسم الشمعة الصاعدة محصور داخل جسم الشمعة الهابطة السابقة"]};}},
    {id:"bearish-harami",name:"نموذج Harami هابط",family:"candles",evaluate:c=>{const a=c.candles.at(-2),b=c.last,ao=Number(a.open),ac=Number(a.close),bo=Number(b.open),bc=Number(b.close),ok=ac>ao&&bc<bo&&bo<ac&&bc>ao;return {signal:ok?SIGNALS.SELL:SIGNALS.WAIT,confidence:ok?65:0,reasons:["جسم الشمعة الهابطة محصور داخل جسم الشمعة الصاعدة السابقة"]};}},
    {id:"bollinger-squeeze-breakout",name:"انفراج بولنجر بعد الانكماش",family:"breakout",evaluate:c=>{const r=c.candles.map(x=>Number(x.close)),band=i=>{const v=r.slice(i-19,i+1),m=v.reduce((a,b)=>a+b,0)/20,sd=Math.sqrt(v.reduce((a,b)=>a+(b-m)**2,0)/20);return {m,sd,width:m?4*sd/m:0};},now=band(r.length-1),old=band(r.length-6),squeeze=old.width>0&&now.width>old.width*1.2&&old.width<.012;return {signal:squeeze&&c.close>now.m+2*now.sd?SIGNALS.BUY:squeeze&&c.close<now.m-2*now.sd?SIGNALS.SELL:SIGNALS.WAIT,confidence:squeeze?70:0,reasons:["اتساع عرض بولنجر بعد انكماش سابق مع تأكيد إغلاق خارج النطاق"]};}},
    {id:"donchian-midline",name:"اتجاه منتصف قناة دونشيان",family:"trend",evaluate:c=>{const r=c.candles.slice(-20),mid=(Math.max(...r.map(x=>Number(x.high)))+Math.min(...r.map(x=>Number(x.low))))/2,prev=c.candles.slice(-25,-5),old=(Math.max(...prev.map(x=>Number(x.high)))+Math.min(...prev.map(x=>Number(x.low))))/2;return {signal:c.close>mid&&mid>old?SIGNALS.BUY:c.close<mid&&mid<old?SIGNALS.SELL:SIGNALS.WAIT,confidence:c.close>mid&&mid>old||c.close<mid&&mid<old?62:0,reasons:["موقع السعر وميل منتصف قناة دونشيان 20"]};}},
    {id:"ema-ribbon-alignment",name:"اصطفاف شريط EMA 8/13/21/34",family:"trend",evaluate:c=>{const v=c.candles.map(x=>Number(x.close)),a=ema(v,8),b=ema(v,13),d=ema(v,21),e=ema(v,34),up=a>b&&b>d&&d>e,down=a<b&&b<d&&d<e;return {signal:up?SIGNALS.BUY:down?SIGNALS.SELL:SIGNALS.WAIT,confidence:up||down?70:0,reasons:["اصطفاف أربعة متوسطات أسية قصيرة ومتوسطة"]};}},
    {id:"rsi-divergence-regular",name:"الدايفرجنس الكلاسيكي RSI",family:"reversal",evaluate:c=>{const r=c.candles.slice(-40),cl=r.map(x=>Number(x.close)),rs=rsiSeries(cl,14),lowA=Math.min(...cl.slice(0,20)),lowB=Math.min(...cl.slice(20)),iA=cl.indexOf(lowA),iB=20+cl.slice(20).indexOf(lowB),highA=Math.max(...cl.slice(0,20)),highB=Math.max(...cl.slice(20)),jA=cl.indexOf(highA),jB=20+cl.slice(20).indexOf(highB),bull=lowB<lowA&&rs[iB]>rs[iA],bear=highB>highA&&rs[jB]<rs[jA];return {signal:bull?SIGNALS.BUY:bear?SIGNALS.SELL:SIGNALS.WAIT,confidence:bull||bear?68:0,reasons:["مقارنة قمم/قيعان السعر مع RSI14 عبر نافذتين"]};}},
    {id:"stochastic-kd-cross",name:"تقاطع Stochastic %K/%D",family:"momentum",evaluate:c=>{const r=c.candles,now=stochasticKD(r,r.length-1),prev=stochasticKD(r,r.length-2),buy=prev.k<=prev.d&&now.k>now.d&&now.k<35,sell=prev.k>=prev.d&&now.k<now.d&&now.k>65;return {signal:buy?SIGNALS.BUY:sell?SIGNALS.SELL:SIGNALS.WAIT,confidence:buy||sell?67:0,reasons:["تقاطع %K و%D مع فلتر مناطق التشبع"]};}},
    {id:"volume-spike-confirmation",name:"تأكيد اندفاع حجم التداول",family:"volume",evaluate:c=>{const r=c.candles,vol=x=>Number(x.tick_volume??x.volume??NaN),v=vol(r.at(-1)),base=r.slice(-21,-1).map(vol).filter(Number.isFinite),avg=base.length?base.reduce((a,b)=>a+b,0)/base.length:0,body=c.close-Number(c.last.open),valid=Number.isFinite(v)&&avg>0,spike=valid&&v>avg*1.8;return {signal:spike&&body>0?SIGNALS.BUY:spike&&body<0?SIGNALS.SELL:SIGNALS.WAIT,confidence:spike?65:0,reasons:[valid?"حجم الشمعة "+v+" مقابل متوسط "+avg.toFixed(1):"بيانات Tick Volume غير متاحة؛ لا يمكن تأكيد الحجم"]};}},
    {id:"atr-squeeze-release",name:"انكماش ATR ثم توسع التقلب",family:"volatility",evaluate:c=>{const a=atrSeries(c.candles,14),now=a.at(-1),prior=a.slice(-11,-1).filter(Number.isFinite),avg=prior.length?prior.reduce((x,y)=>x+y,0)/prior.length:0,compressed=avg>0&&prior.slice(-5).every(x=>x<avg*.85),release=compressed&&now>avg*1.05,body=c.close-Number(c.last.open);return {signal:release&&body>0?SIGNALS.BUY:release&&body<0?SIGNALS.SELL:SIGNALS.WAIT,confidence:release?68:0,reasons:["فحص انكماش ATR14 السابق ثم توسع التقلب الحالي"]};}}
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
  batchFourStrategies.forEach(register);

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
