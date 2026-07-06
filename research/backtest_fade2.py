"""
Backtest MECÁNICO limpio de la regla de selección de izzyaussie:
  a T=180s, si el spike de BTC es DIMINUTO (<=$8), comprar el lado BARATO del mercado
  (el que el precio dejó atrás sin justificación de BTC). Hold a resolución.
Precio de entrada: paginando hasta los trades reales de ~180s (arregla el bug anterior).
"""
import urllib.request, json, sys, time, csv, os, statistics
try: sys.stdout.reconfigure(encoding="utf-8")
except: pass
D = os.path.dirname(__file__)
T=180; SPIKE_MAX=8; COST=0.01

def get(url, tries=3):
    for i in range(tries):
        try:
            req=urllib.request.Request(url, headers={"User-Agent":"bt2/1.0"})
            with urllib.request.urlopen(req, timeout=25) as r: return json.load(r)
        except Exception:
            if i==tries-1: return None
            time.sleep(0.4*(i+1))

addr=[]
for r in csv.DictReader(open(os.path.join(D,"btc_trades.csv"),encoding="utf-8")):
    if r.get("name") in ("izzyaussie","13mm-wrench","wwwise") and r["wallet"] not in addr:
        addr.append(r["wallet"])
cids={}
for w in addr:
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
                cids[cid]=(ws, 900 if "-15m-" in slug else 300)
        if len(tr)<500: break
        off+=500; time.sleep(0.08)
print(f"cids: {len(cids)}")

def spike_at(ws,wlen):
    k=get(f"https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval=1s&startTime={ws*1000}&endTime={(ws+wlen)*1000}&limit=1000")
    if not k or len(k)<10: return None
    s=[(int(c[0])//1000,float(c[4])) for c in k]; o=s[0][1]
    best=None;bd=9999
    for tt,p in s:
        if abs(tt-(ws+T))<bd: bd=abs(tt-(ws+T));best=p
    return (best-o) if best is not None and bd<20 else None

def prices_180(cid,ws):
    up=dn=None; ud=dd=9999; off=0
    for _ in range(6):
        tr=get(f"https://data-api.polymarket.com/trades?market={cid}&limit=500&offset={off}")
        if not isinstance(tr,list) or not tr: break
        for t in tr:
            ts=int(t.get("timestamp") or 0); d=abs(ts-(ws+T))
            if t.get("outcome")=="Up" and d<ud: ud=d; up=float(t.get("price") or 0)
            if t.get("outcome")=="Down" and d<dd: dd=d; dn=float(t.get("price") or 0)
        if int(tr[-1].get("timestamp") or 0) < ws+T: break
        off+=500; time.sleep(0.06)
    return (up,dn) if (ud<75 and dd<75) else (None,None)

def winner_of(cid):
    d=get(f"https://clob.polymarket.com/markets/{cid}")
    if not d: return None
    for t in d.get("tokens",[]):
        if t.get("winner") is True or float(t.get("price") or 0)>=0.95: return t.get("outcome")
    return None

bets=[]
for cid,(ws,wlen) in cids.items():
    if len(bets)>=180: break
    sp=spike_at(ws,wlen); time.sleep(0.04)
    if sp is None or abs(sp)>SPIKE_MAX: continue          # solo spike diminuto
    up,dn=prices_180(cid,ws); time.sleep(0.03)
    if up is None or dn is None: continue
    cheap = "Up" if up<dn else "Down"; cp = min(up,dn)
    if not (0.20<=cp<=0.49): continue
    win=winner_of(cid); time.sleep(0.03)
    if win not in ("Up","Down"): continue
    won=(cheap==win); pe=cp+COST
    bets.append({"ws":ws,"won":won,"pnl":(1/pe-1) if won else -1,"cp":cp})
    if len(bets)%20==0: print(f"  {len(bets)} apuestas…")

n=len(bets)
print(f"\n{'='*58}\n  BACKTEST 'FADE SPIKE DIMINUTO' (regla de selección)\n{'='*58}")
if n:
    wr=sum(b['won'] for b in bets)/n; ev=sum(b['pnl'] for b in bets)/n
    print(f"  apuestas: {n} | WIN {wr:.0%} | precio medio {statistics.mean(b['cp'] for b in bets):.3f} | EV neto {ev:+.1%}")
    bets.sort(key=lambda b:b['ws']); h=n//2
    for lab,seg in [("TRAIN",bets[:h]),("TEST (OOS)",bets[h:])]:
        if seg: print(f"    {lab}: n={len(seg)} | WIN {sum(b['won'] for b in seg)/len(seg):.0%} | EV {sum(b['pnl'] for b in seg)/len(seg):+.1%}")
    print(f"\n  VEREDICTO: {'COPIABLE (+EV en OOS)' if ev>0.02 else 'NO — la regla mecánica no da el 71%; hay otro filtro oculto'}")
else:
    print("  sin apuestas suficientes")
