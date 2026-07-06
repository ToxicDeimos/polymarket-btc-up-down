"""
Simula ser MAKER en ventanas de spike diminuto: postea un bid X por debajo del
mercado en el lado barato y mide FILL RATE + win de los fills (selección adversa)
+ EV neto. ¿La ventaja de fill sobrevive cuando el maker somos nosotros?
"""
import urllib.request, json, sys, time, csv, os, statistics
from collections import Counter
try: sys.stdout.reconfigure(encoding="utf-8")
except: pass
D=os.path.dirname(__file__); T=180; SPIKE_MAX=8

def get(url,tries=3):
    for i in range(tries):
        try:
            req=urllib.request.Request(url,headers={"User-Agent":"ms/1.0"})
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
        off+=500;time.sleep(0.05)
print(f"cids: {len(cids)}",flush=True)

def spike_at(ws,wlen):
    k=get(f"https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval=1s&startTime={ws*1000}&endTime={(ws+wlen)*1000}&limit=1000")
    if not k or len(k)<10: return None
    s=[(int(c[0])//1000,float(c[4])) for c in k];o=s[0][1]
    best=None;bd=9999
    for tt,p in s:
        if abs(tt-(ws+T))<bd: bd=abs(tt-(ws+T));best=p
    return (best-o) if best is not None and bd<20 else None
def market(cid,ws):
    """precio de cada lado a 180 + min precio de SELL de cada lado DESPUÉS de 180."""
    px={};pd={"Up":9999,"Down":9999};minsell={"Up":9,"Down":9};off=0
    for _ in range(8):
        tr=get(f"https://data-api.polymarket.com/trades?market={cid}&limit=500&offset={off}")
        if not isinstance(tr,list) or not tr: break
        for t in tr:
            ts=int(t.get("timestamp") or 0);o=t.get("outcome");p=float(t.get("price") or 0)
            d=abs(ts-(ws+T))
            if o in pd and d<pd[o]: pd[o]=d;px[o]=p
            if ts>ws+T and t.get("side")=="SELL" and o in minsell: minsell[o]=min(minsell[o],p)
        if int(tr[-1].get("timestamp") or 0)<ws: break
        off+=500;time.sleep=getattr(time,"sleep");time.sleep(0.05)
    if pd["Up"]>75 or pd["Down"]>75: return None
    return px,minsell
def winner_of(cid):
    d=get(f"https://clob.polymarket.com/markets/{cid}")
    if not d: return None
    for t in d.get("tokens",[]):
        if t.get("winner") is True or float(t.get("price") or 0)>=0.95: return t.get("outcome")
    return None

OFFS=[0.02,0.05,0.08]
res={o:{"posted":0,"filled":0,"won":0,"ev":0.0} for o in OFFS}
nwin=0
for cid,(ws,wlen) in cids.items():
    if nwin>=200: break
    sp=spike_at(ws,wlen);time.sleep(0.03)
    if sp is None or abs(sp)>SPIKE_MAX: continue
    m=market(cid,ws);time.sleep(0.02)
    if not m: continue
    px,minsell=m
    cheap="Up" if px["Up"]<px["Down"] else "Down"; cp=px[cheap]
    if not (0.20<=cp<=0.49): continue
    win=winner_of(cid);time.sleep(0.02)
    if win not in ("Up","Down"): continue
    nwin+=1; won=1 if cheap==win else 0
    for o in OFFS:
        bid=cp-o
        if bid<0.05: continue
        res[o]["posted"]+=1
        if minsell[cheap]<=bid:                    # alguien vendió a mi bid -> me lleno
            res[o]["filled"]+=1; res[o]["won"]+=won
            res[o]["ev"]+=(1/bid-1) if won else -1
    if nwin%25==0: print(f"  {nwin} ventanas…",flush=True)

print(f"\n{'='*64}\n  MAKER SIM (ventanas spike diminuto, n={nwin})\n{'='*64}")
print(f"  {'bid bajo mkt':>12} | {'fill rate':>9} | {'win de fills':>12} | {'EV/fill':>8}")
for o in OFFS:
    r=res[o]
    if r["filled"]<10:
        print(f"  -{o*100:.0f}¢: fills insuficientes ({r['filled']})"); continue
    fr=r["filled"]/r["posted"]; wr=r["won"]/r["filled"]; ev=r["ev"]/r["filled"]
    print(f"  -{o*100:>4.0f}¢       | {fr:>8.0%} | {wr:>11.0%} | {ev:>+7.0%}")
print("\n  (win de fills << 50% = selección adversa te come la ventaja del bid barato)")
