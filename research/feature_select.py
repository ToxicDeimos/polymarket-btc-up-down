"""
Reconstruir la SELECCIÓN de izzyaussie/13mm-wrench: ¿qué distingue sus fades ganadores?
Para cada fade real, mide features observables al entrar y cruza con win rate:
  - tendencia BTC previa (15/30/60 min antes de la ventana)  -> ¿fadean contra-tendencia?
  - tamaño del spike intra-ventana
  - alineación de su apuesta con cada tendencia
La feature que DISCRIMINA ganar/perder = su señal de selección.
"""
import urllib.request, json, sys, time, csv, os, statistics
from collections import defaultdict
try: sys.stdout.reconfigure(encoding="utf-8")
except: pass
D = os.path.dirname(__file__)

def get(url, tries=3):
    for i in range(tries):
        try:
            req=urllib.request.Request(url, headers={"User-Agent":"fs/1.0"})
            with urllib.request.urlopen(req, timeout=25) as r: return json.load(r)
        except Exception:
            if i==tries-1: return None
            time.sleep(0.4*(i+1))

addr={}
for r in csv.DictReader(open(os.path.join(D,"btc_trades.csv"),encoding="utf-8")):
    if r.get("name") in ("izzyaussie","13mm-wrench"): addr[r["name"]]=r["wallet"]

def klines(start, end, interval):
    k=get(f"https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval={interval}"
          f"&startTime={start*1000}&endTime={end*1000}&limit=1000")
    return [(int(c[0])//1000,float(c[4])) for c in k] if k else []
def at(s,t):
    best=None;bd=99999
    for tt,p in s:
        if abs(tt-t)<bd: bd=abs(tt-t);best=p
    return best

allfades=[]
for name,w in addr.items():
    trades=[];off=0
    while off<=2500:
        tr=get(f"https://data-api.polymarket.com/trades?user={w}&limit=500&offset={off}")
        if not isinstance(tr,list) or not tr: break
        for t in tr:
            slug=t.get("slug","") or ""
            if "btc-updown" not in slug: continue
            try: ws=int(slug.split("-")[-1])
            except: ws=0
            wlen=900 if "-15m-" in slug else 300
            trades.append((t.get("conditionId"),t.get("outcome"),t.get("side"),
                           float(t.get("size") or 0),int(t.get("timestamp") or 0),ws,wlen))
        if len(tr)<500: break
        off+=500;time.sleep(0.08)
    byw=defaultdict(lambda:{"Up":0.0,"Down":0.0,"ts":9e18,"ws":0,"wlen":900})
    for cid,o,side,size,ts,ws,wlen in trades:
        m=byw[cid];m["ws"]=ws;m["wlen"]=wlen
        m[o]+= size if side=="BUY" else -size; m["ts"]=min(m["ts"],ts)
    for cid,m in sorted(byw.items(),key=lambda kv:-kv[1]["ws"])[:60]:
        d=get(f"https://clob.polymarket.com/markets/{cid}");time.sleep(0.04)
        win=None
        if d:
            for t in d.get("tokens",[]):
                if t.get("winner") is True or float(t.get("price") or 0)>=0.95: win=t.get("outcome");break
        if win not in ("Up","Down"): continue
        ws=m["ws"]
        ins=klines(ws, ws+m["wlen"], "1s");time.sleep(0.04)
        pre=klines(ws-3600, ws, "1m");time.sleep(0.04)
        if len(ins)<10 or len(pre)<20: continue
        o=ins[0][1]; e=at(ins, ws+180)
        if e is None: continue
        spike=e-o
        bet="Up" if m["Up"]-m["Down"]>0 else "Down"
        follows=(bet=="Up" and spike>0) or (bet=="Down" and spike<0)
        if follows: continue      # solo FADES
        pw=pre[-1][1]
        tr15=pw-at(pre,ws-900); tr30=pw-at(pre,ws-1800); tr60=pw-at(pre,ws-3600)
        allfades.append({"bet":bet,"won":bet==win,"spike":abs(spike),
                         "tr15":tr15,"tr30":tr30,"tr60":tr60})

n=len(allfades)
print(f"\n{'='*62}\n  SELECCIÓN: ¿qué distingue sus fades ganadores? (n={n})\n{'='*62}")
def wr(sub): return f"{sum(x['won'] for x in sub)/len(sub):.0%} (n={len(sub)})" if sub else "-"
print(f"  win rate global: {wr(allfades)}\n")
for lb in ("tr15","tr30","tr60"):
    al=[x for x in allfades if (x['bet']=='Up' and x[lb]>0) or (x['bet']=='Down' and x[lb]<0)]
    ag=[x for x in allfades if x not in al]
    print(f"  tendencia previa {lb[2:]}min:")
    print(f"    apuesta A FAVOR de la tendencia (fade contra-tendencia): {wr(al)}")
    print(f"    apuesta CONTRA la tendencia:                            {wr(ag)}")
print("\n  por tamaño de spike:")
for lo,hi in [(0,8),(8,15),(15,40)]:
    sub=[x for x in allfades if lo<=x['spike']<hi]
    print(f"    spike ${lo}-{hi}: {wr(sub)}")
