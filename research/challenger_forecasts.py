"""Optional forecasting challengers, imported only by research worker.
No broker/terminal endpoints or server dependencies.
"""
import math,json,time,os

LOCAL=("TiRex-2","Chronos-Bolt Tiny")
CLOUD={"Qwen 3.8 27B":"qwen/qwen3.8-27b",
       "GPT-OSS 120B (Arena)":"openai/gpt-oss-120b"}

def finite_predictions(values,expected):
    result=[float(x) for x in values]
    if len(result)!=expected or not all(math.isfinite(x) for x in result):
        raise ValueError("invalid forecast")
    return result

def forecast_local(name,contexts):
    if name=="TiRex-2" and os.name=="nt":os.environ.setdefault("TORCH_COMPILE_DISABLE","1")
    import torch
    torch.set_num_threads(1)
    inputs=[torch.as_tensor(x,dtype=torch.float32) for x in contexts]
    if name=="TiRex-2":
        from tirex2 import load_model,TimeseriesType
        model=load_model("NX-AI/TiRex-2",device="cpu")
        ts=[TimeseriesType(target=x.unsqueeze(0),past_covariates=None,future_covariates=None) for x in inputs]
        values=[]
        for i in range(0,len(ts),4):
            batch=model.forecast(ts[i:i+4],prediction_length=3,output_type="numpy",pad_context=False,batch_size=4)
            values.extend(float(a[0,4,2]) for a in batch)
        return finite_predictions(values,len(contexts)),"NX-AI/TiRex-2"
    if name=="Chronos-Bolt Tiny":
        from chronos import BaseChronosPipeline
        model=BaseChronosPipeline.from_pretrained("amazon/chronos-bolt-tiny",device_map="cpu",torch_dtype=torch.float32)
        values=[]
        for i in range(0,len(inputs),4):
            q,_=model.predict_quantiles(inputs[i:i+4],prediction_length=3,quantile_levels=[.5])
            values.extend(float(a[2,0]) for a in q)
        return finite_predictions(values,len(contexts)),"amazon/chronos-bolt-tiny"
    raise ValueError("unknown challenger")

def cloud_forecast(name,contexts,key,request=None):
    """One capped, explicit cloud research call per origin. Only normalized
    price contexts are sent, no account IDs, credentials, positions or labels.
    Errors never become fabricated WAITs. Caller must opt in to API charges.
    """
    if name not in CLOUD or not key or len(contexts)>48:raise ValueError("cloud pilot limit")
    if request is None:
        from groq import Groq
        client=Groq(api_key=key,max_retries=0,timeout=30.0)
        def send(body):
            return client.chat.completions.create(**body).model_dump()
        call=send
    else:call=request
    values=[]
    for i,context in enumerate(contexts):
        if i and request is None:time.sleep(3)
        payload={"model":CLOUD[name],"max_completion_tokens":1024,
            "messages":[{"role":"system","content":"Research only. Forecast normalized price at t+3 from observed data. Last price is zero, units ATR. Reply JSON only: {\"forecast\": number}. Never issue an order."},
                        {"role":"user","content":json.dumps([round(float(x),5) for x in context],allow_nan=False)}]}
        response=call(payload)
        parsed=json.loads(response["choices"][0]["message"]["content"])
        value=parsed.get("forecast")
        if isinstance(value,bool) or not isinstance(value,(float,int)) or not math.isfinite(value):raise ValueError("invalid cloud forecast")
        values.append(max(-6.,min(6.,float(value))))
    return finite_predictions(values,len(contexts)),CLOUD[name]
