# Gold replacement benchmark — 2026-11-26

Status: RESEARCH ONLY. No production approval or order authorization.
Data: 12,000 owner-scoped XAUUSD M1 bars through 11:39 UAE; 10,483 contiguous eligible windows, chronological train/validation/test 6,284/2,092/2,097, purged 5-minute label overlap. Test signals spaced at least five minutes. Entry next open, exit third close; side-specific spread proxy; stress adds another spread and 0.1 ATR. Cooling uses fifth close and 0.6 adverse excursion penalty. These are price-path proxies, not actual basket profits.

## S12 replacement candidates
| Rule | Validation net | Validation stress | Test net | Test stress |
|---|---:|---:|---:|---:|
| S12 RSI + EMA slope | -32.521 | -101.430 | -57.428 | -118.673 |
| RSI + candle direction | -28.492 | -95.083 | 13.050 | -46.031 |
| EMA retest | -14.799 | -69.825 | -10.642 | -59.898 |
| EMA breakout | -11.500 | -48.152 | -14.931 | -48.937 |
| Liquidity sweep | -18.530 | -37.359 | -11.512 | -28.139 |
| Wick rejection | -38.458 | -78.902 | -23.543 | -64.654 |

No candidate passed validation and stress; no catalog ID is reassigned. Benchmark is standalone rules (not full MASTER execution); RSI/candle uses S12 parameters. Ridge direction challenger (four ridge penalties x five cutoffs) also produced no positive stress-validation candidate.

## SELL cooling alternatives
| Rule | Validation signals | Validation net | Test signals | Test net | Test stress |
|---|---:|---:|---:|---:|---:|
| Original adverse context (unfiltered) | 207 | -199.182 | 227 | -195.556 | -232.644 |
| Wick | 53 | -28.926 | 56 | -64.002 | -72.661 |
| Sweep | 26 | -32.027 | 37 | -33.677 | -39.648 |
| Trend retest | 22 | -7.456 | 26 | -44.477 | -48.691 |
| EMA breakout | 3 | -3.396 | 0 | 0 | 0 |
| RSI candle | 32 | -11.600 | 39 | -65.088 | -71.380 |

No qualified replacement. The 11:14 training report of existing SELL cooling specialist was -66.3455 proxy on 57 selected test signals; different evaluation, not directly comparable with the unfiltered context above. Do not treat omission of a negative specialist score as permission to add exposure.

## Direction model replacement
Four-model candidate excluding Chronos-Bolt Tiny remains shadow only (commit 30d6df2b). Previous chronological replay of 54 new matured windows: existing -1.674 ATR on 1 selected signal, four-model -0.516 ATR on 4; five-model proportional -1.585 ATR on 3; three-model -2.269 ATR on 5. None positive at 180 seconds. Isolated copied shadow-function test executed 12/12 cases including retired models, contradictory forecasts, strategy/price/trap guard, absence, and no execution authorization; not end-to-end testing.

Kronos-small/base are present as research benchmarks in repository; no current matched outcomes prove either is superior. Keep old learning records, symbol isolation and all active entry rules unchanged pending qualified evidence.
