"""Wire-only payload reduction. No trading, auth, or timing decisions."""
import gzip
import urllib.parse

def accepts_gzip(value):
    for part in str(value or "").split(","):
        bits=part.strip().lower().split(";")
        if bits[0].strip()!="gzip":continue
        q=1.
        try:
            for param in bits[1:]:
                k,sep,v=param.partition("=")
                if k.strip()=="q" and sep:q=float(v.strip())
        except ValueError:continue
        if 0.<q<=1.:return True
    return False

def negotiate_payload(raw,path,accept_encoding,kind):
    path=urllib.parse.urlsplit(path).path
    if "/v1/" in path or len(raw)<1024 or not accepts_gzip(accept_encoding):
        return raw,False
    if not any(t in kind.lower() for t in ("json","text/","javascript")):
        return raw,False
    encoded=gzip.compress(raw,compresslevel=3)
    return (encoded,True) if len(encoded)<len(raw) else (raw,False)

def unchanged_context(ctx,params):
    if ctx.get("status")!="READY":return ctx
    if (params.get("seen_client")==[str(ctx.get("client"))] and
        params.get("seen_symbol")==[ctx.get("symbol")] and
        params.get("seen_bar")==[str(ctx.get("bar_time"))]):
        return {"status":"UNCHANGED","client":ctx["client"],"symbol":ctx["symbol"],"bar_time":ctx["bar_time"]}
    return ctx
