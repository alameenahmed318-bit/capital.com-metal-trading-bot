"""Owner-only local export, read-only. Never upload candles or credentials to GitHub."""
import argparse,getpass,json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from model_arena_worker import login,archive

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--out",required=True)
    a=p.parse_args()
    out=Path(a.out).expanduser().resolve()
    repo=Path(__file__).resolve().parents[2]
    if out==repo or repo in out.parents:raise ValueError("Choose a private directory outside the Git repository")
    out.mkdir(parents=True,exist_ok=False)
    email=input("Owner email: ").strip()
    token=login(email,getpass.getpass("Owner password: "))
    fields=["time","open","high","low","close","tick_volume","spread_points","point_size"]
    for market in ("XAUUSD","BTCUSD"):
        _,bars=archive(market,token)
        rows=[[b[k] for k in fields] for b in bars if b["point_size"]>0 and b["spread_points"]>0]
        if len(rows)<800:raise ValueError(market+" insufficient candles")
        path=out/(market+".json")
        path.write_text(json.dumps({"symbol":market,"columns":fields,"bars":rows},allow_nan=False),encoding="utf-8")
        print(market,len(rows),"candles saved locally",flush=True)
if __name__=="__main__":main()
