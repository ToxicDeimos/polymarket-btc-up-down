"""
Backtest MECÁNICO OOS de la señal de zmbabwe: a T=275s, si BTC se movió >$MOVE desde
la apertura, apostar el lado ALINEADO (momentum) al precio de mercado. ¿Win > precio OOS?
"""
import urllib.request, json, sys, time, csv, os, math, statistics
from collections import Counter
try: sys.stdout.reconfigure(encoding="utf-8")
except: pass
D=os.path.dirname(__file__); T=275; MOVE=8; COST=0.01

def get(url,tries=3):
    for i in range(tries):
        try:
            req=urllib.request.Request(url,headers={"User-Agent":"mb/1.0"})
            with urllib.request.urlopen(req,timeout=25) as r: return json.load(r)
        except Exception:
            if i==tries-1: return None
            time.sleep(0.35*(i+1))

cnt=Counter()
for r in csv.DictReader(open(os.path.join(D,"btc_trades.csv"),encoding="utf-8")): cnt[r["wallet"]]+=1
cids={}
for w,_ in cnt.most_common(60):
    off=0
    while off<=4000:
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

def btc(ws,wlen):
    k=get(f"https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval=1s&startTime={ws*1000}&endTime={(ws+wlen)*1000}&limit=1000")
    if not k or len(k)<10: return None,None
    s=[(int(c[0])//1000,float(c[4])) for c in k];o=s[0][1]
    best=None;bd=9999
    for tt,p in s:
        if abs(tt-(ws+T))<bd: bd=abs(tt-(ws+T));best=p
    return (o,best) if best is not None and bd<20 else (None,None)
def price_at(cid,ws,side):
    best=None;bd=9999;off=0
    for _ in range(6):
        tr=get(f"https://data-api.polymarket.com/trades?market={cid}&limit=500&offset={off}")
        if not isinstance(tr,list) or not tr: break
        for t in tr:
            if t.get("outcome")==side:
                d=abs(int(t.get("timestamp") or 0)-(ws+T))
                if d<bd: bd=d;best=float(t.get("price") or 0)
        if int(tr[-1].get("timestamp") or 0)<ws+T: break
        off+=500;time.sleep(0.05)
    return best if bd<90 else None
def winner_of(cid):
    d=get(f"https://clob.polymarket.com/markets/{cid}")
    if not d: return None
    for t in d.get("tokens",[]):
        if t.get("winner") is True or float(t.get("price") or 0)>=0.95: return t.get("outcome")
    return None

bets=[]
for cid,(ws,wlen) in cids.items():
    if len(bets)>=250: break
    if wlen<T+30: continue                       # ventana debe durar > 275s
    o,e=btc(ws,wlen);time.sleep(0.03)
    if o is None or abs(e-o)<MOVE: continue       # BTC se movió >$8
    side="Up" if e-o>0 else "Down"                # momentum
    p=price_at(cid,ws,side);time.sleep(0.02)
    if p is None or not (0.4<=p<=0.9): continue
    win=winner_of(cid);time.sleep(0.02)
    if win not in ("Up","Down"): continue
    bets.append((ws,p,1 if side==win else 0))
    if len(bets)%25==0: print(f"  {len(bets)} apuestas…",flush=True)

n=len(bets)
print(f"\n{'='*56}\n  MOMENTUM BACKTEST (bet lado alineado a 275s, n={n})\n{'='*56}")
if n>=30:
    wr=sum(b[2] for b in bets)/n; ap=statistics.mean(b[1] for b in bets)
    se=math.sqrt(wr*(1-wr)/n)
    ev=sum((1/(b[1]+COST)-1) if b[2] else -1 for b in bets)/n
    print(f"  WIN {wr:.1%} (IC: {wr-1.96*se:.1%}-{wr+1.96*se:.1%}) | precio {ap:.1%} | EV {ev:+.1%}")
    bets.sort();h=n//2
    for lab,seg in [("TRAIN",bets[:h]),("TEST",bets[h:])]:
        print(f"    {lab}: n={len(seg)} WIN {sum(b[2] for b in seg)/len(seg):.0%} precio {statistics.mean(b[1] for b in seg):.0%}")
    if wr-1.96*se>ap: print("  → EDGE MOMENTUM CONFIRMADO (win > precio significativo)")
    elif wr>ap: print("  → positivo, no significativo")
    else: print("  → el mercado ya precia el momentum (win <= precio)")
