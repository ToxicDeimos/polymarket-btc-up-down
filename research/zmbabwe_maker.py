"""¿zmbabwe también entra más barato que el mercado (maker)? Clava la tesis 'edge=ejecución'."""
import urllib.request, json, sys, time, os, statistics
from collections import defaultdict
try: sys.stdout.reconfigure(encoding="utf-8")
except: pass
W="0xdfd4ab76f0c86c6dd913d60ccceaff4eaac591f7"; me={W}

def get(url,tries=3):
    for i in range(tries):
        try:
            req=urllib.request.Request(url,headers={"User-Agent":"zk/1.0"})
            with urllib.request.urlopen(req,timeout=25) as r: return json.load(r)
        except Exception:
            if i==tries-1: return None
            time.sleep(0.4*(i+1))

trades=[];off=0
while off<=3000:
    tr=get(f"https://data-api.polymarket.com/trades?user={W}&limit=500&offset={off}")
    if not isinstance(tr,list) or not tr: break
    for t in tr:
        slug=t.get("slug","") or ""
        if "btc-updown" not in slug: continue
        try: ws=int(slug.split("-")[-1])
        except: ws=0
        trades.append((t.get("conditionId"),t.get("outcome"),t.get("side"),float(t.get("price") or 0),float(t.get("size") or 0),int(t.get("timestamp") or 0),ws))
    if len(tr)<500: break
    off+=500;time.sleep(0.06)
byw=defaultdict(lambda:{"Up":0.0,"Down":0.0,"ws":0,"ts":9e18,"pxs":defaultdict(float),"pxv":defaultdict(float)})
for cid,o,side,price,size,ts,ws in trades:
    m=byw[cid];m["ws"]=ws;m[o]+= size if side=="BUY" else -size;m["ts"]=min(m["ts"],ts)
    if side=="BUY": m["pxs"][o]+=price*size;m["pxv"][o]+=size

diffs=[]
for cid,m in sorted(byw.items(),key=lambda kv:-kv[1]["ws"])[:70]:
    bet="Up" if m["Up"]-m["Down"]>0 else "Down"
    if m["pxv"][bet]<=0: continue
    zp=m["pxs"][bet]/m["pxv"][bet]
    tr=get(f"https://data-api.polymarket.com/trades?market={cid}&limit=500");time.sleep(0.04)
    if not isinstance(tr,list): continue
    s=v=0.0
    for t in tr:
        if t.get("outcome")==bet and t.get("side")=="BUY" and t.get("proxyWallet") not in me and abs(int(t.get("timestamp") or 0)-m["ts"])<120:
            sz=float(t.get("size") or 0);s+=float(t.get("price") or 0)*sz;v+=sz
    if v<=0: continue
    diffs.append((zp,s/v))
n=len(diffs)
print(f"\n{'='*50}\n  zmbabwe: ¿maker? (n={n})\n{'='*50}")
if n:
    zp=statistics.mean(d[0] for d in diffs);mk=statistics.mean(d[1] for d in diffs)
    adv=statistics.mean(d[1]-d[0] for d in diffs);ch=sum(1 for d in diffs if d[1]>d[0])/n
    print(f"  precio zmbabwe: {zp:.3f} | precio mercado: {mk:.3f}")
    print(f"  entra más barato en {ch:.0%} de ventanas | ventaja media {adv*100:+.1f}¢")
    print(f"  → {'MAKER confirmado: gana por ejecución, no por señal' if adv>0.02 else 'NO es más barato (edge distinto)'}")
