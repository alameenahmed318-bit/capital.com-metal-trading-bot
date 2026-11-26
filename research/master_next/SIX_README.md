# Six candidate development trials
No changes to production, no private candles, account identifiers or secrets in this public branch. Research-only.
- Kronos-small official pretrained weights + locally trained Ridge calibration. Not full Kronos fine-tuning.
- Chronos-2 32-step LoRA pilot on training-only windows.
- Official HF PatchTST architecture, trained from scratch for 20 epochs.
- Moirai 2.0-R-small frozen weights + Ridge calibration; CC BY-NC 4.0, research only, not approved for trading deployment.
- Discrete IQL adaptation and CQL, 2000 updates each, based on simulated behavior data.
- Compact return-conditioned Decision Transformer, 1500 updates.
Public fixtures were already inspected; none of these tests establishes independent profitability. Cost stress uses proxy costs and M1 candles; no exact production strategy comparison. Separate methods and different position horizons, so compare each against relevant baseline, not raw P&L across tasks. Some external checkpoint installations or CPU memory requirements may fail; report actual status. No automatic deployment or retraining.
