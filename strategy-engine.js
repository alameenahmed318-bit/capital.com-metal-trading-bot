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
      const result = strategy.evaluate(context);
      return { id: strategy.id, name: strategy.name, family: strategy.family,
        vote: result.signal, why: result.reasons.join("؛ "), confidence: result.confidence };
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
