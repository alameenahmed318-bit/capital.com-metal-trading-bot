"""Single-console, local-only launcher for the seven candidate advisers.
Separate child processes keep model inference and training isolated.
Credentials stay in process memory; no order endpoints or persistent credentials.
"""
import argparse
import getpass
import multiprocessing as mp
import time
from urllib.error import HTTPError

FAST=("Extra Trees","CatBoost")
FORECAST=("TiRex-2","Chronos-Bolt Tiny")
PARALLEL=("TCN","GRU","Patch Transformer")

def run_worker(lane,email,password,session,guard,market,epochs):
    import model_arena_worker as arena
    original_login=arena.login
    original_api=arena.api
    arena.MARKETS=("BTCUSD","XAUUSD") if market=="BOTH" else (market,)
    def shared_login(_email,_password):
        with guard:
            now=time.monotonic()
            if not session.get("token") or now>=session.get("expires",0):
                session["token"]=original_login(email,password)
                session["expires"]=time.monotonic()+1200
            return session["token"]
    def shared_api(market_name,path,_token,body=None,timeout=45):
        token=shared_login(email,password)
        try:
            return original_api(market_name,path,token,body,timeout=timeout)
        except HTTPError as exc:
            print(lane,market_name,path.split("?")[0],"HTTP",exc.code,flush=True)
            if exc.code!=401:
                raise
            # Refresh once only on expired credentials. Never bypass 403.
            with guard:
                if session.get("token")==token:session["expires"]=0
            return original_api(market_name,path,shared_login(email,password),body,timeout=timeout)
    arena.login=shared_login
    arena.api=shared_api
    try:
        if lane=="ARENA":
            import arena_live_worker as worker
            worker.DIRECTION_TREES=FAST
            worker.PRETRAINED=FORECAST
            worker.live(email,password)
        else:
            import parallel_shadow_worker as worker
            worker.NAMES=PARALLEL
            worker.USE_ANOMALY=False
            worker.main(email,password,["--market",market,"--epochs",str(epochs)])
    except KeyboardInterrupt:
        pass
    except Exception as exc:
        print(lane,"STOPPED",type(exc).__name__,flush=True)
        raise

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--market",choices=["BOTH","XAUUSD","BTCUSD"],default="BOTH")
    p.add_argument("--epochs",type=int,default=8)
    args=p.parse_args()
    if not 1<=args.epochs<=40:p.error("epochs must be 1..40")
    import model_arena_worker as arena
    email=input("Owner email: ").strip()
    password=getpass.getpass("Owner password: ")
    token=arena.login(email,password)
    print("ALZOOL UNIFIED ADVISERS |",", ".join(FAST+FORECAST+PARALLEL),flush=True)
    print("One console | separate workers | no order permission | Ctrl+C stops both",flush=True)
    ctx=mp.get_context("spawn")
    with ctx.Manager() as manager:
        session=manager.dict(token=token,expires=time.monotonic()+1200)
        guard=manager.Lock()
        children=[]
        try:
            for lane in ("ARENA","PARALLEL"):
                proc=ctx.Process(target=run_worker,args=(lane,email,password,session,guard,args.market,args.epochs),name="ALZOOL-"+lane)
                proc.start();children.append(proc)
            while any(proc.is_alive() for proc in children):
                for proc in children:
                    if proc.exitcode is not None and proc.exitcode!=0:
                        print(proc.name,"exited with",proc.exitcode,"; other worker continues",flush=True)
                        children.remove(proc)
                        break
                time.sleep(2)
        except KeyboardInterrupt:
            print("Stopping unified advisers",flush=True)
        finally:
            for proc in children:
                if proc.is_alive():proc.terminate()
            for proc in children:proc.join(timeout=5)
            session.clear()

if __name__=="__main__":
    mp.freeze_support()
    main()
