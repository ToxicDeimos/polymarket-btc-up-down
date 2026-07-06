"""
Valida el SELECTOR (modelo) en datos FRESCOS. Entrena con rich.csv (l2=1.0 moderado),
aplica a ventanas nunca vistas. ¿Las apuestas que elige el modelo son +EV fuera de muestra?
"""
import urllib.request, json, sys, time, csv, os, math, statistics
from collections import Counter, defaultdict
from datetime import datetime, timezone
import numpy as np
try: sys.stdout.reconfigure(encoding="utf-8")
except: pass
D=os.path.dirname(__file__); T=180; SPIKE_CAP=30
FEATS=["wlen","spike","abs_spike","vol_pre","peak_dev","reverting","btc_cont60",
       "cheap_price","divergence","buy_cheap","sell_cheap","buy_exp","sell_exp",
       "flow_imb","sellpress","n_trades","total_vol","hour"]

# ── entrenar ──
rows=[r for r in csv.DictReader(open(os.path.join(D,"rich.csv"),encoding="utf-8")) if r.get("won") in("0","1")]
def num(r,k):
    try: return float(r[k])
    except: return 0.0
X=np.array([[num(r,k) for k in FEATS] for r in rows]); y=np.array([int(r["won"]) for r in rows])
mu,sd=X.mean(0),X.std(0)+1e-9; Xs=(X-mu)/sd
def fit(X,y,l2=1.0,lr=0.2,it=5000):
    n,d=X.shape;w=np.zeros(d);b=0.0
    for _ in range(it):
        p=1/(1+np.exp(-(X@w+b)));w-=lr*(X.T@(p-y)/n+l2*w/n);b-=lr*(p-y).mean()
    return w,b
w,b=fit(Xs,y)
used=set(int(r["ws"]) for r in rows)
print(f"modelo entrenado con {len(rows)} ventanas",flush=True)

def get(url,tries=3):
    for i in range(tries):
        try:
            req=urllib.request.Request(url,headers={"User-Agent":"vm/1.0"})
            with urllib.request.urlopen(req,timeout=25) as r: return json.load(r)
        except Exception:
            if i==tries-1: return None
            time.sleep(0.35*(i+1))
def btc(ws,wlen):
    k=get(f"https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval=1s&startTime={ws*1000}&endTime={(ws+wlen)*1000}&limit=1000")
    return [(int(c[0])//1000,float(c[4])) for c in k] if k and len(k)>=10 else None
def at(s,t):
    bb=None;bd=9999
    for tt,p in s:
        if abs(tt-t)<bd: bd=abs(tt-t);bb=p
    return bb
def prewin(ws):
    k=get(f"https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval=1m&startTime={(ws-1800)*1000}&endTime={ws*1000}&limit=100")
    pr=[float(c[4]) for c in k] if k else []
    return statistics.pstdev([pr[i]-pr[i-1] for i in range(1,len(pr))]) if len(pr)>5 else None
def mkt(cid,ws):
    px={};pd={"Up":9999,"Down":9999};vol=defaultdict(float);nt=0;tv=0.0;off=0
    for _ in range(8):
        tr=get(f"https://data-api.polymarket.com/trades?market={cid}&limit=500&offset={off}")
        if not isinstance(tr,list) or not tr: break
        for t in tr:
            ts=int(t.get("timestamp") or 0);o=t.get("outcome");sz=float(t.get("size") or 0)
            if o in pd and abs(ts-(ws+T))<pd[o]: pd[o]=abs(ts-(ws+T));px[o]=float(t.get("price") or 0)
            if ws<=ts<=ws+180: vol[(o,t.get("side"))]+=sz;nt+=1;tv+=sz
        if int(tr[-1].get("timestamp") or 0)<ws: break
        off+=500;time.sleep(0.05)
    return (px,vol,nt,tv) if pd["Up"]<75 and pd["Down"]<75 else None
def winner_of(cid):
    d=get(f"https://clob.polymarket.com/markets/{cid}")
    if not d: return None
    for t in d.get("tokens",[]):
        if t.get("winner") is True or float(t.get("price") or 0)>=0.95: return t.get("outcome")
    return None

cnt=Counter()
for r in csv.DictReader(open(os.path.join(D,"btc_trades.csv"),encoding="utf-8")): cnt[r["wallet"]]+=1
wallets=[x for x,_ in cnt.most_common(90)][20:]
cids={}
for wl in wallets:
    off=0
    while off<=6000:
        tr=get(f"https://data-api.polymarket.com/trades?user={wl}&limit=500&offset={off}")
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

bets=[]; nwin=0
for cid,(ws,wlen) in cids.items():
    if nwin>=260: break
    s=btc(ws,wlen);time.sleep(0.03)
    if not s: continue
    o=s[0][1];e=at(s,ws+T);spike=e-o
    if abs(spike)>SPIKE_CAP: continue
    peak=max((abs(p-o) for tt,p in s if tt<=ws+T),default=0)
    cont=max((((at(s,ws+T+k)-e) if spike>0 else (e-at(s,ws+T+k))) for k in range(1,61) if at(s,ws+T+k) is not None),default=0)
    m=mkt(cid,ws);time.sleep(0.02)
    if not m: continue
    px,vol,nt,tv=m
    cheap="Up" if px["Up"]<px["Down"] else "Down";exp="Down" if cheap=="Up" else "Up";cp=px[cheap]
    if not (0.15<=cp<=0.5): continue
    vp=prewin(ws);time.sleep(0.02)
    if vp is None: continue
    win=winner_of(cid);time.sleep(0.02)
    if win not in ("Up","Down"): continue
    nwin+=1
    bc=vol[(cheap,"BUY")];sc=vol[(cheap,"SELL")];be=vol[(exp,"BUY")]
    hour=datetime.fromtimestamp(ws,timezone.utc).hour
    feat={"wlen":wlen,"spike":spike,"abs_spike":abs(spike),"vol_pre":vp,"peak_dev":peak,
          "reverting":1 if abs(spike)<0.6*peak else 0,"btc_cont60":cont,"cheap_price":cp,
          "divergence":abs(px["Up"]-px["Down"]),"buy_cheap":bc,"sell_cheap":sc,"buy_exp":be,
          "sell_exp":vol[(exp,"SELL")],"flow_imb":be/(bc+1),"sellpress":sc/(bc+sc+1),
          "n_trades":nt,"total_vol":tv,"hour":hour}
    xv=(np.array([feat[k] for k in FEATS])-mu)/sd
    prob=1/(1+np.exp(-(xv@w+b)))
    if prob>cp:                                   # SELECTOR: modelo dice fadear
        bets.append((cp,1 if cheap==win else 0))
    if nwin%20==0: print(f"  {nwin} ventanas ({len(bets)} elegidas)…",flush=True)

nb=len(bets)
print(f"\n{'='*58}\n  SELECTOR (MODELO) EN DATOS FRESCOS\n{'='*58}")
print(f"  ventanas evaluadas: {nwin} | el modelo eligió fadear: {nb}")
if nb>=30:
    wr=sum(x[1] for x in bets)/nb; ap=statistics.mean(x[0] for x in bets)
    se=math.sqrt(wr*(1-wr)/nb)
    ev=sum((1/x[0]-1) if x[1] else -1 for x in bets)/nb
    print(f"  WIN {wr:.1%} (IC95%: {wr-1.96*se:.1%}-{wr+1.96*se:.1%}) | precio {ap:.1%} | EV {ev:+.1%}")
    if wr-1.96*se>ap: print("  → CONFIRMADO: el selector bate el precio en datos frescos. SELECTOR REAL.")
    elif wr>ap: print("  → positivo pero no significativo")
    else: print("  → NO confirma: se desinfla (el held-out era régimen)")
else: print("  muestra insuficiente")
