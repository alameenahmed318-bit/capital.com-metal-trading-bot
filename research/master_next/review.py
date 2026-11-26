"""Optional one-call cloud report review; never grants order authority."""
import argparse,json,os,re,urllib.request
from pathlib import Path

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--report",required=True);p.add_argument("--provider",choices=["openai","gemini"],required=True)
    p.add_argument("--model");p.add_argument("--allow-paid-api",action="store_true")
    a=p.parse_args()
    if not a.allow_paid_api:raise SystemExit("Explicit paid API opt-in required")
    r=json.loads(Path(a.report).read_text(encoding="utf-8"))
    if r.get("version")!="MASTER_NEXT_RESEARCH_V1" or r.get("execution_authorized") is not False:raise ValueError("Unexpected report")
    # Only known numeric metrics leave the laptop, never account identifiers or news texts.
    summary={}
    for phase in ("validation","test"):
        summary[phase]={n:{k:{m:float(d[m]) for m in ("net_atr_units","max_drawdown_atr_units","turnover","fills")} for k,d in v.items() if k in ("base","stress")} for n,v in r[phase].items() if n in ("PPO","DQN","CASH","MOMENTUM")}
    prompt="Review these simulated metrics. Identify overfitting and missing tests. Do not recommend actual trades or change settings. "+json.dumps(summary,allow_nan=False)
    if a.provider=="openai":
        key=os.environ["OPENAI_API_KEY"];url="https://api.openai.com/v1/chat/completions"
        headers={"Authorization":"Bearer "+key}
        body={"model":a.model or "gpt-4o","messages":[{"role":"user","content":prompt}],"max_tokens":600}
    else:
        key=os.environ["GEMINI_API_KEY"];model=a.model
        if not model or not re.fullmatch(r"[A-Za-z0-9._-]+",model):raise ValueError("Specify an available Gemini model ID")
        url="https://generativelanguage.googleapis.com/v1beta/models/"+model+":generateContent"
        headers={"x-goog-api-key":key}
        body={"contents":[{"parts":[{"text":prompt}]}],"generationConfig":{"maxOutputTokens":600}}
    headers["Content-Type"]="application/json"
    req=urllib.request.Request(url,data=json.dumps(body).encode(),headers=headers,method="POST")
    with urllib.request.urlopen(req,timeout=30) as response:result=json.load(response)
    if a.provider=="openai":answer=result["choices"][0]["message"]["content"]
    else:answer="\n".join(p.get("text","") for p in result["candidates"][0]["content"]["parts"])
    print(answer)
if __name__=="__main__":main()
