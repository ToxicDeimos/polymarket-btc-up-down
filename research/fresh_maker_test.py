"""
TEST OOS FRESCO del MAKER DINÁMICO pre-registrado:
  spike<=8 -> bid a (cheap-2c) en el lado barato; CANCELAR si BTC continua el spike
  > $5 en [180,240]; si no, llenar si alguien vende <= bid. Hold a resolucion.
Excluye ventanas ya usadas (features.csv). Umbral $5 pre-registrado (no el mejor).
"""
import urllib.request, json, sys, time, csv, os, math, statistics
from collections import Counter
try: sys.stdout.reconfigure(encoding="utf-8")
except: pass
D=os.path.dirname(__file__); T=180; SPIKE_MAX=8; BID_OFF=0.02; CANCEL=5

def get(url,tries=3):
    for i in range(tries):
        try:
            req=urllib.request.Request(url,headers={"User-Agent":"fmt/1.0"})
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
wallets=[w for w,_ in cnt.most_common(90)][20:]
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

def btc(ws,wlen):
    k=get(f"https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval=1s&startTime={ws*1000}&endTime={(ws+wlen)*1000}&limit=1000")
    return [(int(c[0])//1000,float(c[4])) for c in k] if k and len(k)>=10 else None
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

fills=[]; ncancel=nwin=0
for cid,(ws,wlen) in cids.items():
    if nwin>=220: break
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
    nwin+=1; bid=cp-BID_OFF
    if bid<0.05: continue
    cont=0
    for k in range(1,61):
        p=at(s,ws+T+k)
        if p is None: continue
        cont=max(cont,(p-e) if spike>0 else (e-p))
    if cont>CANCEL: ncancel+=1; continue            # CANCELADO
    if minsell[cheap]<=bid: fills.append((bid,1 if cheap==win else 0))
    if nwin%20==0: print(f"  {nwin} ventanas…",flush=True)

nf=len(fills)
print(f"\n{'='*58}\n  MAKER DINÁMICO EN DATOS FRESCOS\n{'='*58}")
print(f"  ventanas: {nwin} | canceladas: {ncancel} ({ncancel/max(nwin,1):.0%}) | fills: {nf}")
if nf>=30:
    wr=sum(f[1] for f in fills)/nf; ap=statistics.mean(f[0] for f in fills)
    se=math.sqrt(wr*(1-wr)/nf); lo,hi=wr-1.96*se,wr+1.96*se
    ev=sum((1/f[0]-1) if f[1] else -1 for f in fills)/nf
    print(f"  WIN de fills {wr:.1%} (IC95%: {lo:.1%}-{hi:.1%}) | bid medio {ap:.1%} | EV/fill {ev:+.1%}")
    if lo>ap: print("  → CONFIRMADO: el maker dinámico gana significativamente por encima del bid")
    elif wr>ap: print("  → POSITIVO pero no significativo (IC toca break-even)")
    else: print("  → NO se confirma: se desinfla en fresco")
else: print("  fills insuficientes")
