"""
Maker DINÁMICO: postea bid a 180s en el lado barato (fade), pero CANCELA si BTC
continúa el spike en [180,240] (el fade va a perder). ¿Evitar esos fills sube el
win de fills de 41% hacia 50%+ (esquivando la selección adversa)?
"""
import urllib.request, json, sys, time, csv, os
from collections import Counter
try: sys.stdout.reconfigure(encoding="utf-8")
except: pass
D=os.path.dirname(__file__); T=180; SPIKE_MAX=8; BID_OFF=0.02

def get(url,tries=3):
    for i in range(tries):
        try:
            req=urllib.request.Request(url,headers={"User-Agent":"ms2/1.0"})
            with urllib.request.urlopen(req,timeout=25) as r: return json.load(r)
        except Exception:
            if i==tries-1: return None
            time.sleep(0.35*(i+1))

cnt=Counter()
for r in csv.DictReader(open(os.path.join(D,"btc_trades.csv"),encoding="utf-8")): cnt[r["wallet"]]+=1
cids={}
for w,_ in cnt.most_common(60):
    off=0
    while off<=3000:
        tr=get(f"https://data-api.polymarket.com/trades?user={w}&limit=500&offset={off}")
        if not isinstance(tr,list) or not tr: break
        for t in tr:
            slug=t.get("slug","") or ""
            if "btc-updown" not in slug: continue
            cid=t.get("conditionId")
            if cid not in cids:
                try: ws=int(slug.split("-")[-1])
                except: ws=0
                cids[cid]=(ws,900 if "-15m-" in slug else 300)
        if len(tr)<500: break
        off+=500;time.sleep=getattr(time,"sleep");time.sleep(0.05)
print(f"cids: {len(cids)}",flush=True)

def btc(ws,wlen):
    k=get(f"https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval=1s&startTime={ws*1000}&endTime={(ws+wlen)*1000}&limit=1000")
    if not k or len(k)<10: return None
    return [(int(c[0])//1000,float(c[4])) for c in k]
def at(s,t):
    best=None;bd=9999
    for tt,p in s:
        if abs(tt-t)<bd: bd=abs(tt-t);best=p
    return best
def market(cid,ws):
    px={};pd={"Up":9999,"Down":9999};minsell={"Up":9,"Down":9};off=0
    for _ in range(8):
        tr=get(f"https://data-api.polymarket.com/trades?market={cid}&limit=500&offset={off}")
        if not isinstance(tr,list) or not tr: break
        for t in tr:
            ts=int(t.get("timestamp") or 0);o=t.get("outcome");p=float(t.get("price") or 0)
            if o in pd and abs(ts-(ws+T))<pd[o]: pd[o]=abs(ts-(ws+T));px[o]=p
            if ts>ws+T and t.get("side")=="SELL" and o in minsell: minsell[o]=min(minsell[o],p)
        if int(tr[-1].get("timestamp") or 0)<ws: break
        off+=500;time.sleep(0.05)
    return (px,minsell) if pd["Up"]<75 and pd["Down"]<75 else None
def winner_of(cid):
    d=get(f"https://clob.polymarket.com/markets/{cid}")
    if not d: return None
    for t in d.get("tokens",[]):
        if t.get("winner") is True or float(t.get("price") or 0)>=0.95: return t.get("outcome")
    return None

THRESH=[3,5,8]
base={"filled":0,"won":0}
dyn={x:{"cancel":0,"filled":0,"won":0,"ev":0.0} for x in THRESH}
nwin=0
for cid,(ws,wlen) in cids.items():
    if nwin>=200: break
    s=btc(ws,wlen);time.sleep(0.03)
    if not s: continue
    o=s[0][1]; e=at(s,ws+T); spike=e-o
    if abs(spike)>SPIKE_MAX: continue
    m=market(cid,ws);time.sleep(0.02)
    if not m: continue
    px,minsell=m
    cheap="Up" if px["Up"]<px["Down"] else "Down"; cp=px[cheap]
    if not (0.20<=cp<=0.49): continue
    win=winner_of(cid);time.sleep(0.02)
    if win not in ("Up","Down"): continue
    bid=cp-BID_OFF
    if bid<0.05: continue
    nwin+=1; won=1 if cheap==win else 0; fills = minsell[cheap]<=bid
    # continuación del spike en [180,240] (BTC sigue en la dirección del spike)
    cont=0
    for k in range(1,61):
        p=at(s,ws+T+k)
        if p is None: continue
        c=(p-e) if spike>0 else (e-p)     # positivo = sigue el spike
        cont=max(cont,c)
    if fills: base["filled"]+=1; base["won"]+=won
    for x in THRESH:
        if cont>x: dyn[x]["cancel"]+=1; continue     # cancelamos el bid
        if fills: dyn[x]["filled"]+=1; dyn[x]["won"]+=won; dyn[x]["ev"]+=(1/bid-1) if won else -1
    if nwin%25==0: print(f"  {nwin} ventanas…",flush=True)

print(f"\n{'='*64}\n  MAKER DINÁMICO (cancela si BTC continúa el spike, n={nwin})\n{'='*64}")
if base["filled"]:
    print(f"  ESTÁTICO (sin cancelar): win de fills {base['won']/base['filled']:.0%} (fills={base['filled']})\n")
print(f"  {'cancela si cont >':>17} | {'% cancelado':>11} | {'win de fills':>12} | {'EV/fill':>8}")
for x in THRESH:
    r=dyn[x]
    if r["filled"]<10: print(f"  ${x}: fills insuficientes"); continue
    print(f"  ${x:>3}              | {r['cancel']/nwin:>10.0%} | {r['won']/r['filled']:>11.0%} | {r['ev']/r['filled']:>+7.0%}")
print("\n  (si el win de fills sube hacia 50%+ al cancelar → el maker DINÁMICO esquiva la selección adversa)")
