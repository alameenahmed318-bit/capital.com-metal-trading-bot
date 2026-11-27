"""Isolated two-adviser retest. Read-only server access, no forecast publication.
The existing unified worker can keep running. No shared state is modified.
"""
import argparse,getpass,time,os
os.environ.setdefault("OMP_NUM_THREADS","1")
os.environ.setdefault("MKL_NUM_THREADS","1")
import model_arena_worker as arena
import arena_live_worker as trees
import parallel_shadow_worker as parallel

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--market",choices=["BOTH","XAUUSD","BTCUSD"],default="BOTH")
    p.add_argument("--epochs",type=int,default=8)
    args=p.parse_args()
    if not 1<=args.epochs<=40:p.error("epochs must be 1..40")
    email=input("Owner email: ").strip()
    password=getpass.getpass("Owner password: ")
    token=arena.login(email,password)
    trees.DIRECTION_TREES=("XGBoost",)
    parallel.NAMES=("LSTM Multi",)
    parallel.USE_ANOMALY=False
    print("ISOLATED RETEST | XGBoost + LSTM Multi | read only | no forecast publication",flush=True)
    for market in (("XAUUSD","BTCUSD") if args.market=="BOTH" else (args.market,)):
        try:
            ctx=arena.api(market,"live-context",token)
            if ctx.get("status")!="READY":
                print(market,"NO_READY_CONTEXT",flush=True);continue
            cid,symbol=ctx["client"],ctx["symbol"]
            rows=parallel.exact_archive(market,token,cid,symbol)
            print(market,"ARCHIVE",len(rows),flush=True)
            models=trees.train_direction(rows,market)
            print(market,"XGBoost","PASSED" if "XGBoost" in models else "NOT_VALIDATED",flush=True)
            evidence=arena.api(market,"parallel-learning?"+parallel.urllib.parse.urlencode({"client":cid,"symbol":symbol}),token)
            if evidence.get("symbol")!=symbol:raise ValueError("wrong evidence symbol")
            fitted=parallel.fit(parallel.array(rows[-6000:]),args.epochs,(),evidence.get("slippage_points"))
            threshold=fitted[2].get("LSTM Multi",float("inf"))
            print(market,"LSTM Multi","PASSED" if parallel.math.isfinite(threshold) else "NOT_VALIDATED",flush=True)
        except Exception as exc:
            print(market,"RETEST_ERROR",type(exc).__name__,getattr(exc,"code",""),flush=True)
    print("RETEST_COMPLETE | no running worker or trading settings changed",flush=True)

if __name__=="__main__":main()
