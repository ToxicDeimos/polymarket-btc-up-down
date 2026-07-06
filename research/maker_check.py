"""
¿izzyaussie entra más barato que el mercado (maker) o paga el precio (taker)?
Compara su precio de compra vs el precio medio de OTROS que compran el mismo lado
cerca de 180s. Si el suyo es menor → ventaja maker (postea bids, no cruza).
"""
import urllib.request, json, sys, time, csv, os, statistics
from collections import defaultdict
try: sys.stdout.reconfigure(encoding="utf-8")
except: pass
D=os.path.dirname(__file__)

def get(url,tries=3):
    for i in range(tries):
        try:
            req=urllib.request.Request(url,headers={"User-Agent":"mk/1.0"})
            with urllib.request.urlopen(req,timeout=25) as r: return json.load(r)
        except Exception:
            if i==tries-1: return None
            time.sleep(0.35*(i+1))

addr={}
for r in csv.DictReader(open(os.path.join(D,"btc_trades.csv"),encoding="utf-8")):
    if r.get("name") in ("izzyaussie","13mm-wrench"): addr[r["name"]]=r["wallet"]
me=set(addr.values())

# ventanas de izzyaussie: su lado + su precio medio + ws
def iz_windows(w):
    trades=[];off=0
    while off<=2000:
        tr=get(f"https://data-api.polymarket.com/trades?user={w}&limit=500&offset={off}")
        if not isinstance(tr,list) or not tr: break
        for t in tr:
            slug=t.get("slug","") or ""
            if "btc-updown" not in slug: continue
            try: ws=int(slug.split("-")[-1])
            except: ws=0
            trades.append((t.get("conditionId"),t.get("outcome"),t.get("side"),float(t.get("price") or 0),float(t.get("size") or 0),ws))
        if len(tr)<500: break
        off+=500;time.sleep(0.06)
    byw=defaultdict(lambda:{"Up":0.0,"Down":0.0,"ws":0,"pxsum":defaultdict(float),"pxvol":defaultdict(float)})
    for cid,o,side,price,size,ws in trades:
        m=byw[cid];m["ws"]=ws;m[o]+= size if side=="BUY" else -size
        if side=="BUY": m["pxsum"][o]+=price*size; m["pxvol"][o]+=size
    return byw

diffs=[]
for name,w in addr.items():
    byw=iz_windows(w)
    for cid,m in sorted(byw.items(),key=lambda kv:-kv[1]["ws"])[:50]:
        bet="Up" if m["Up"]-m["Down"]>0 else "Down"
        if m["pxvol"][bet]<=0: continue
        iz_price=m["pxsum"][bet]/m["pxvol"][bet]           # precio medio de izzyaussie en su lado
        # precio de OTROS comprando ese lado cerca de 180s
        tr=get(f"https://data-api.polymarket.com/trades?market={cid}&limit=500");time.sleep=getattr(time,"sleep");time.sleep(0.04)
        if not isinstance(tr,list): continue
        s=v=0.0
        for t in tr:
            ts=int(t.get("timestamp") or 0)
            if t.get("outcome")==bet and t.get("side")=="BUY" and t.get("proxyWallet") not in me and abs(ts-(m["ws"]+180))<120:
                sz=float(t.get("size") or 0); s+=float(t.get("price") or 0)*sz; v+=sz
        if v<=0: continue
        mkt_price=s/v
        diffs.append((iz_price,mkt_price,mkt_price-iz_price))

n=len(diffs)
print(f"\n{'='*56}\n  VENTAJA MAKER (n={n} ventanas)\n{'='*56}")
if n:
    iz=statistics.mean(d[0] for d in diffs); mk=statistics.mean(d[1] for d in diffs)
    adv=statistics.mean(d[2] for d in diffs); cheaper=sum(1 for d in diffs if d[2]>0)/n
    print(f"  precio medio izzyaussie:  {iz:.3f}")
    print(f"  precio medio del MERCADO: {mk:.3f}  (otros, mismo lado, ~180s)")
    print(f"  entra MÁS BARATO en {cheaper:.0%} de las ventanas | ventaja media: {adv*100:+.1f}¢")
    print(f"\n  Efecto en EV (a win rate 55%):")
    print(f"    comprando a {iz:.3f} (izzy):    EV {0.55/iz-1:+.0%}")
    print(f"    comprando a {mk:.3f} (mercado): EV {0.55/mk-1:+.0%}")
    print(f"  → la diferencia de {adv*100:.1f}¢ aporta ~{(0.55/iz-1)-(0.55/mk-1):+.0%} de EV solo por el fill")
