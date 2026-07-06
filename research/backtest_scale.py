"""
Backtest ESCALADO de 'fadea spike diminuto, compra el lado barato' para confirmar
si el +EV es real o ruido. Cosecha cids de las wallets más activas → cientos de
ventanas con spike <=$8 → win rate con INTERVALO DE CONFIANZA vs break-even.
"""
import urllib.request, json, sys, time, csv, os, math, statistics
from collections import Counter
try: sys.stdout.reconfigure(encoding="utf-8")
except: pass
D=os.path.dirname(__file__); T=180; SPIKE_MAX=8; COST=0.01
OUT=os.path.join(D,"backtest_scale_bets.csv")

def get(url,tries=3):
    for i in range(tries):
        try:
            req=urllib.request.Request(url,headers={"User-Agent":"bts/1.0"})
            with urllib.request.urlopen(req,timeout=25) as r: return json.load(r)
        except Exception:
            if i==tries-1: return None
            time.sleep(0.35*(i+1))

# top wallets por actividad
cnt=Counter()
for r in csv.DictReader(open(os.path.join(D,"btc_trades.csv"),encoding="utf-8")):
    cnt[r["wallet"]]+=1
tops=[w for w,_ in cnt.most_common(40)]

# cosechar cids
cids={}
for w in tops:
    off=0
    while off<=2500:
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
        off+=500; time.sleep=getattr(time,"sleep"); time.sleep(0.06)
print(f"cids cosechados: {len(cids)}",flush=True)

def spike_at(ws,wlen):
    k=get(f"https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval=1s&startTime={ws*1000}&endTime={(ws+wlen)*1000}&limit=1000")
    if not k or len(k)<10: return None
    s=[(int(c[0])//1000,float(c[4])) for c in k]; o=s[0][1]
    best=None;bd=9999
    for tt,p in s:
        if abs(tt-(ws+T))<bd: bd=abs(tt-(ws+T));best=p
    return (best-o) if best is not None and bd<20 else None
def prices_180(cid,ws):
    up=dn=None;ud=dd=9999;off=0
    for _ in range(6):
        tr=get(f"https://data-api.polymarket.com/trades?market={cid}&limit=500&offset={off}")
        if not isinstance(tr,list) or not tr: break
        for t in tr:
            ts=int(t.get("timestamp") or 0);d=abs(ts-(ws+T))
            if t.get("outcome")=="Up" and d<ud: ud=d;up=float(t.get("price") or 0)
            if t.get("outcome")=="Down" and d<dd: dd=d;dn=float(t.get("price") or 0)
        if int(tr[-1].get("timestamp") or 0)<ws+T: break
        off+=500;time.sleep(0.05)
    return (up,dn) if (ud<75 and dd<75) else (None,None)
def winner_of(cid):
    d=get(f"https://clob.polymarket.com/markets/{cid}")
    if not d: return None
    for t in d.get("tokens",[]):
        if t.get("winner") is True or float(t.get("price") or 0)>=0.95: return t.get("outcome")
    return None

fh=open(OUT,"w",newline="",encoding="utf-8");w=csv.writer(fh);w.writerow(["ws","cheap","price","won"])
bets=[]
for cid,(ws,wlen) in cids.items():
    if len(bets)>=350: break
    sp=spike_at(ws,wlen);time.sleep(0.03)
    if sp is None or abs(sp)>SPIKE_MAX: continue
    up,dn=prices_180(cid,ws);time.sleep(0.02)
    if up is None or dn is None: continue
    cheap="Up" if up<dn else "Down";cp=min(up,dn)
    if not (0.20<=cp<=0.49): continue
    win=winner_of(cid);time.sleep(0.02)
    if win not in ("Up","Down"): continue
    won=1 if cheap==win else 0
    bets.append((ws,cp,won));w.writerow([ws,cheap,cp,won])
    if len(bets)%25==0: fh.flush();print(f"  {len(bets)} apuestas…",flush=True)
fh.close()

n=len(bets)
print(f"\n{'='*58}\n  BACKTEST ESCALADO (n={n})\n{'='*58}")
if n>=30:
    wins=sum(b[2] for b in bets); wr=wins/n; ap=statistics.mean(b[1] for b in bets)
    se=math.sqrt(wr*(1-wr)/n); lo,hi=wr-1.96*se,wr+1.96*se
    ev=sum((1/(b[1]+COST)-1) if b[2] else -1 for b in bets)/n
    print(f"  WIN {wr:.1%}  (IC95%: {lo:.1%}-{hi:.1%})")
    print(f"  break-even (precio medio): {ap:.1%}")
    print(f"  EV neto/apuesta: {ev:+.1%}")
    print(f"\n  ¿El IC del win rate EXCLUYE el break-even ({ap:.0%})?")
    if lo>ap: print("    SÍ → EDGE REAL confirmado (win rate significativamente > precio)")
    elif hi<ap: print("    NO → win rate por DEBAJO del precio = pierde")
    else: print(f"    NO concluyente → el IC incluye el break-even. Aún ruido, no confirmado.")
    bets.sort();h=n//2
    for lab,seg in [("TRAIN",bets[:h]),("TEST",bets[h:])]:
        wr2=sum(b[2] for b in seg)/len(seg)
        print(f"    {lab}: n={len(seg)} WIN {wr2:.0%}")
