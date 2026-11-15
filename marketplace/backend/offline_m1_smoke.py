"""Read-only weekend XAUUSD M1 smoke: historical quant replay + one Groq review.

Runs only when enabled by the server owner. Never writes market data,
changes settings, accepts an order, or authorizes execution.
"""
import json
import time

from strategy_audit import audit_strategies
from strategy_experiments import _candle, replay_directional
from strategy_engine import evaluate_market
import groq_advisor


def run(read_rows):
    started=time.perf_counter()
    try:
        rows=read_rows()
        valid=[x for row in rows if (x:=_candle(row)) is not None]
        if len(valid)<75:
            print("ALZOOL_HISTORICAL_M1:",json.dumps({"status":"INSUFFICIENT_HISTORY",
                "rows":len(rows),"valid":len(valid),"trading_execution":False}),flush=True)
            return
        audit=audit_strategies(rows,max_samples=18)
        replay=replay_directional(rows,{"strategy_weights":{},"combination_weights":{}},max_samples=30)
        decision=evaluate_market(valid[-120:])
        elapsed_ms=round((time.perf_counter()-started)*1000,1)
        summary={
            "status":"OK","rows":len(rows),"valid":len(valid),
            "strategies":audit.get("strategies_total"),
            "combinations":audit.get("combinations_total"),
            "audit_training_points":audit.get("training_points"),
            "audit_validation_points":audit.get("validation_points"),
            "strategy_candidates":audit.get("strategy_candidates"),
            "combination_candidates":audit.get("combination_candidates"),
            "replay_samples":replay.get("samples"),
            "replay_signals":replay.get("signals"),
            "replay_hits":replay.get("hits"),
            "replay_hit_rate":replay.get("hit_rate"),
            "replay_coverage":replay.get("coverage"),
            "latest_quant_signal":decision.get("decision","WAIT"),
            "quant_analysis_ms":elapsed_ms,
            "execution_authorized":False,
            "accuracy_scope":"NEXT_CANDLE_DIRECTION_ONLY_NO_FILL_OR_SPREAD"
        }
        print("ALZOOL_HISTORICAL_M1:",json.dumps(summary),flush=True)
        if not groq_advisor.configured():
            print("ALZOOL_HISTORICAL_GROQ:",json.dumps(
                {"status":"NOT_CONFIGURED","execution_authorized":False}),flush=True)
            return
        # Only anonymous gold price/indicator evidence. No personal/account data.
        # Explicitly historical: Groq's advisory vote never enters the live council.
        context=decision.get("context") if isinstance(decision.get("context"),dict) else {}
        snapshot={
            "mode":"OFFLINE_HISTORICAL_M1_READ_ONLY",
            "symbol":"XAUUSD",
            "closed_bar_time":str(valid[-1]["time"])[:40],
            "close":valid[-1]["close"],
            "atr":context.get("atr"),
            "strategy_id":decision.get("strategy_id"),
            "quant_direction":decision.get("decision","WAIT"),
            "quant_confidence":decision.get("confidence"),
            "strategy_count":audit.get("strategies_total"),
            "combination_count":audit.get("combinations_total"),
            "family_votes":decision.get("family_votes"),
            "combination_summary":decision.get("combination_summary")
        }
        t0=time.perf_counter()
        try:
            response=groq_advisor._call_groq(snapshot,timeout=12)
            vote=groq_advisor._parse_response(response)
            result={"status":vote.get("status"),"direction":vote.get("direction"),
                    "confidence":vote.get("confidence"),
                    "elapsed_ms":round((time.perf_counter()-t0)*1000,1),
                    "execution_authorized":False}
        except Exception as error:
            # Never print request, response, account identity, or API secrets.
            result={"status":"UNAVAILABLE","error_type":type(error).__name__,
                    "elapsed_ms":round((time.perf_counter()-t0)*1000,1),
                    "execution_authorized":False}
        print("ALZOOL_HISTORICAL_GROQ:",json.dumps(result),flush=True)
    except Exception as error:
        print("ALZOOL_HISTORICAL_M1:",json.dumps(
            {"status":"FAILED","error_type":type(error).__name__,
             "execution_authorized":False}),flush=True)
