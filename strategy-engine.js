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

  const registry = new Map();
  function register(strategy) {
    if (!strategy || !strategy.id || !strategy.name || !strategy.family || typeof strategy.evaluate !== "function")
      throw new TypeError("Invalid strategy contract");
    if (registry.has(strategy.id)) throw new Error("Duplicate strategy id: " + strategy.id);
    registry.set(strategy.id, Object.freeze(strategy));
    return strategy.id;
  }
  strategies.forEach(register);

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
    const signal = buy >= 3 && buy - sell >= 2 ? SIGNALS.BUY :
      sell >= 3 && sell - buy >= 2 ? SIGNALS.SELL : SIGNALS.WAIT;
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
