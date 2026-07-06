"""
Reverse-engineer zmbabwe (perfil DIRECCIONAL, compra ~0.52, gana 58%).
¿Qué predice qué lado apuesta y gana? Momento de BTC al entrar, tendencia previa,
order-flow. La feature que separe sus ganadores = su señal direccional.
"""
import urllib.request, json, sys, time, os, statistics
from collections import defaultdict
try: sys.stdout.reconfigure(encoding="utf-8")
except: pass
W="0xdfd4ab76f0c86c6dd913d60ccceaff4eaac591f7"; T=180

def get(url,tries=3):
    for i in range(tries):
        try:
            req=urllib.request.Request(url,headers={"User-Agent":"zm/1.0"})
            with urllib.request.urlopen(req,timeout=25) as r: return json.load(r)
        except Exception:
            if i==tries-1: return None
            time.sleep(0.4*(i+1))

# ventanas de zmbabwe: lado neto + primera entrada + precio
trades=[];off=0
while off<=3000:
    tr=get(f"https://data-api.polymarket.com/trades?user={W}&limit=500&offset={off}")
    if not isinstance(tr,list) or not tr: break
    for t in tr:
        slug=t.get("slug","") or ""
        if "btc-updown" not in slug: continue
        try: ws=int(slug.split("-")[-1])
        except: ws=0
        trades.append((t.get("conditionId"),t.get("outcome"),t.get("side"),float(t.get("size") or 0),int(t.get("timestamp") or 0),float(t.get("price") or 0),ws,900 if "-15m-" in slug else 300))
    if len(tr)<500: break
    off+=500;time.sleep(0.06)
byw=defaultdict(lambda:{"Up":0.0,"Down":0.0,"ts":9e18,"ws":0,"wlen":900})
for cid,o,side,size,ts,price,ws,wlen in trades:
    m=byw[cid];m["ws"]=ws;m["wlen"]=wlen; m[o]+= size if side=="BUY" else -size; m["ts"]=min(m["ts"],ts)
print(f"ventanas de zmbabwe: {len(byw)}",flush=True)

def kl(start,end,intv):
    k=get(f"https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval={intv}&startTime={start*1000}&endTime={end*1000}&limit=1000")
    return [(int(c[0])//1000,float(c[4])) for c in k] if k else []
def at(s,t):
    b=None;bd=9999
    for tt,p in s:
        if abs(tt-t)<bd: bd=abs(tt-t);b=p
    return b
def winner_of(cid):
    d=get(f"https://clob.polymarket.com/markets/{cid}")
    if not d: return None
    for t in d.get("tokens",[]):
        if t.get("winner") is True or float(t.get("price") or 0)>=0.95: return t.get("outcome")
    return None
def flow(cid,ws,ts):
    v=defaultdict(float);off=0
    for _ in range(6):
        tr=get(f"https://data-api.polymarket.com/trades?market={cid}&limit=500&offset={off}")
        if not isinstance(tr,list) or not tr: break
        for t in tr:
            tt=int(t.get("timestamp") or 0)
            if ws<=tt<=ts: v[(t.get("outcome"),t.get("side"))]+=float(t.get("size") or 0)
        if int(tr[-1].get("timestamp") or 0)<ws: break
        off+=500;time.sleep(0.05)
    return v

recs=[]
for cid,m in sorted(byw.items(),key=lambda kv:-kv[1]["ws"])[:120]:
    bet="Up" if m["Up"]-m["Down"]>0 else "Down"
    win=winner_of(cid);time.sleep(0.03)
    if win not in ("Up","Down"): continue
    s=kl(m["ws"],m["ws"]+m["wlen"],"1s");time.sleep(0.03)
    if len(s)<10: continue
    o=s[0][1];ent=at(s,int(m["ts"]))
    if ent is None: continue
    pre=kl(m["ws"]-1800,m["ws"],"1m");time.sleep(0.03)
    if len(pre)<10: continue
    trend=pre[-1][1]-at(pre,m["ws"]-1800)
    move=ent-o
    v=flow(cid,m["ws"],int(m["ts"]));time.sleep(0.03)
    imb=(v[("Up","BUY")]-v[("Down","BUY")])
    recs.append({"won":bet==win,"bet":bet,
                 "mom":(bet=="Up" and move>0) or (bet=="Down" and move<0),
                 "tr":(bet=="Up" and trend>0) or (bet=="Down" and trend<0),
                 "fl":(bet=="Up" and imb>0) or (bet=="Down" and imb<0)})

n=len(recs)
def wr(s): return f"{sum(x['won'] for x in s)/len(s):.0%} (n={len(s)})" if s else "-"
print(f"\n{'='*56}\n  zmbabwe DIRECCIONAL (n={n})\n{'='*56}")
print(f"  win rate global: {wr(recs)}\n")
for f,lab in [("mom","momento de BTC al entrar"),("tr","tendencia previa 30min"),("fl","order-flow (imbalance)")]:
    print(f"  su apuesta va A FAVOR de {lab}:")
    print(f"    sí: {wr([x for x in recs if x[f]])} | no: {wr([x for x in recs if not x[f]])}")
