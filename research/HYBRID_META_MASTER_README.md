# AL-ZOOL Hybrid Meta Master V1 (research only)

Two independent learned regressors (CatBoost and XGBoost) predict BUY and SELL quote-proxy returns, then a validation-selected blend produces one direction or WAIT. Compare CatBoost, XGBoost and their blend using identical time-ordered, purged train/validation/test splits. The untouched test vetoes the validation winner; a negative stressed result rejects promotion. No production code, permissions, accounts or trading settings are changed. No broker execution path exists.

## Run on the owner's machine

Install compatible numpy, catboost and xgboost in the existing research environment. Run:
```
python research/hybrid_meta_master.py --self-test --out smoke.json
python research/hybrid_meta_master.py --input owner-local-audit.json --symbol XAUUSD --out gold-meta-report.json
python research/hybrid_meta_master.py --input owner-local-audit.json --symbol BTCUSD --out btc-meta-report.json
```

Owner-local audit export: use the database SQL editor (read-only query), export result as JSON locally:
```sql
SELECT payload FROM public.alzool_engine_signal_events
WHERE event_type = 'MASTER_V3_OUTCOME'
ORDER BY created_at;
```
Do not commit exports, credentials, account identifiers or output containing private data. At least 300 distinct matured bars per symbol are needed. The optional parallel_evidence object must contain the exact symbol, bar_time, original received_at timestamp and forecasts present at decision time. It is not safe to reconstruct missing predictions from future observations; leave missing values absent. Historical records with no parallel evidence cannot establish the performance of the proposed replacement advisers. Input timestamps must be original and consistent.

The synthetic self-test verifies the training pipeline only. The 60-second quote proxy already reflects entry/exit quote assumptions; an extra 0.30 ATR stress penalty is an illustrative stress scenario, not actual broker slippage. Research comparisons can overlap historical development data; a positive result still requires independent forward validation, transaction-cost verification, and explicit integration review. No direct promotion, automatic retraining or trading activation is implemented.
