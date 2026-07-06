"""
TEST OOS FRESCO de la regla con order-flow:
  |spike|<=8  AND  buy_favorito > buy_barato en [0,180]  (estampida al favorito)
  -> comprar el lado barato (0.20-0.49) a 180s. Hold a resolución.
Excluye ventanas ya usadas. Pre-registrado.
"""
import urllib.request, json, sys, time, csv, os, math, statistics
from collections import Counter, defaultdict
try: sys.stdout.reconfigure(encoding="utf-8")
except: pass
D=os.path.dirname(__file__); T=180; SPIKE_MAX=8; COST=0.01

def get(url,tries=3):
    for i in range(tries):
        try:
            req=urllib.request.Request(url,headers={"User-Agent":"ft2/1.0"})
            with urllib.request.urlopen(req,timeout=25) as r: return json.load(r)
        except Exception:
            if i==tries-1: return None
            time.sleep(0.35*(i+1))

used=set()
for r in csv.DictReader(open(os.path.join(D,"features.csv"),encoding="utf-8")):
    try: used.add(int(r["ws"]))
    except: pass
cnt=Counter()
for r in csv.DictReader(open(os.path.join(D,"btc_trades.csv"),encoding="utf-8")): cnt[r["wallet"]]+=1
wallets=[w for w,_ in cnt.most_common(90)][25:]
cids={}
for w in wallets:
    off=0
    while off<=6000:
        tr=get(f"https://data-api.polymarket.com/trades?user={w}&limit=500&offset={off}")
        if not isinstance(tr,list) or not tr: break
        for t in tr:
            slug=t.get("slug","") or ""
            if "btc-updown" not in slug: continue
            try: ws=int(slug.split("-")[-1])
            except: ws=0
            if ws in used: continue
            cid=t.get("conditionId")
            if cid not in cids: cids[cid]=(ws,900 if "-15m-" in slug else 300)
        if len(tr)<500: break
        off+=500;time.sleep(0.05)
print(f"cids frescos: {len(cids)}",flush=True)

def spike_at(ws,wlen):
    k=get(f"https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval=1s&startTime={ws*1000}&endTime={(ws+wlen)*1000}&limit=1000")
    if not k or len(k)<10: return None
    s=[(int(c[0])//1000,float(c[4])) for c in k];o=s[0][1]
    best=None;bd=9999
    for tt,p in s:
        if abs(tt-(ws+T))<bd: bd=abs(tt-(ws+T));best=p
    return (best-o) if best is not None and bd<20 else None
def flow_price(cid,ws):
    """flujo buy/sell por outcome en [0,180] + precio de cada lado cerca de 180."""
    vol=defaultdict(float); px={}; pd={"Up":9999,"Down":9999}; off=0
    for _ in range(8):
        tr=get(f"https://data-api.polymarket.com/trades?market={cid}&limit=500&offset={off}")
        if not isinstance(tr,list) or not tr: break
        for t in tr:
            ts=int(t.get("timestamp") or 0);o=t.get("outcome")
            if ws<=ts<=ws+180: vol[(o,t.get("side"))]+=float(t.get("size") or 0)
            d=abs(ts-(ws+T))
            if o in pd and d<pd[o]: pd[o]=d; px[o]=float(t.get("price") or 0)
        if int(tr[-1].get("timestamp") or 0)<ws: break
        off+=500;time.sleep(0.05)
    if pd["Up"]>75 or pd["Down"]>75: return None
    return vol,px
def winner_of(cid):
    d=get(f"https://clob.polymarket.com/markets/{cid}")
    if not d: return None
    for t in d.get("tokens",[]):
        if t.get("winner") is True or float(t.get("price") or 0)>=0.95: return t.get("outcome")
    return None

bets=[]
for cid,(ws,wlen) in cids.items():
    if len(bets)>=220: break
    sp=spike_at(ws,wlen);time.sleep(0.03)
    if sp is None or abs(sp)>SPIKE_MAX: continue
    fp=flow_price(cid,ws);time.sleep(0.02)
    if not fp: continue
    vol,px=fp
    cheap="Up" if px["Up"]<px["Down"] else "Down"; exp="Down" if cheap=="Up" else "Up"; cp=px[cheap]
    if not (0.20<=cp<=0.49): continue
    buy_cheap=vol[(cheap,"BUY")]; buy_exp=vol[(exp,"BUY")]
    if not (buy_exp>buy_cheap): continue                  # LA REGLA: estampida al favorito
    win=winner_of(cid);time.sleep(0.02)
    if win not in ("Up","Down"): continue
    bets.append((cp,1 if cheap==win else 0))
    if len(bets)%20==0: print(f"  {len(bets)} apuestas…",flush=True)

n=len(bets)
print(f"\n{'='*56}\n  REGLA CON ORDER-FLOW EN DATOS FRESCOS (n={n})\n{'='*56}")
if n>=30:
    wr=sum(b[1] for b in bets)/n; ap=statistics.mean(b[0] for b in bets)
    se=math.sqrt(wr*(1-wr)/n); lo,hi=wr-1.96*se,wr+1.96*se
    ev=sum((1/(b[0]+COST)-1) if b[1] else -1 for b in bets)/n
    print(f"  WIN {wr:.1%} (IC95%: {lo:.1%}-{hi:.1%}) | break-even {ap:.1%} | EV {ev:+.1%}")
    if lo>ap: print("  → CONFIRMADO: win rate significativamente > precio en datos frescos")
    elif wr>ap: print("  → POSITIVO pero no significativo (IC toca break-even)")
    else: print("  → NO se confirma (se desinfla como el vol)")
else: print("  muestra insuficiente")
