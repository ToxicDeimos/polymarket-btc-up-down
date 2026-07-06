"""
Backtest OUT-OF-SAMPLE de la regla exacta de los ganadores:
  a T=180s, si BTC hizo un spike de $SPIKE_LO-SPIKE_HI desde la apertura,
  FADEAR: comprar el lado que BTC dejó atrás (el barato). Hold a resolución.
Aplica la regla MECÁNICAMENTE a un conjunto amplio de ventanas y mide EV neto + OOS.
"""
import urllib.request, json, sys, time, csv, os, statistics
from collections import defaultdict
try: sys.stdout.reconfigure(encoding="utf-8")
except: pass
D = os.path.dirname(__file__)
T_ENTRY = 180
SPIKE_LO, SPIKE_HI = 5, 25
COST = 0.01

def get(url, tries=3):
    for i in range(tries):
        try:
            req=urllib.request.Request(url, headers={"User-Agent":"bt/1.0"})
            with urllib.request.urlopen(req, timeout=25) as r: return json.load(r)
        except Exception:
            if i==tries-1: return None
            time.sleep(0.4*(i+1))

# Direcciones para cosechar cids (operan casi todas las ventanas → set amplio)
addr=[]
for r in csv.DictReader(open(os.path.join(D,"btc_trades.csv"),encoding="utf-8")):
    if r.get("name") in ("izzyaussie","13mm-wrench","wwwise") and r["wallet"] not in addr:
        addr.append(r["wallet"])

cids={}   # cid -> (wstart, wlen)
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
                wlen=900 if "-15m-" in slug else 300
                try: ws=int(slug.split("-")[-1])
                except: ws=0
                cids[cid]=(ws,wlen)
        if len(tr)<500: break
        off+=500; time.sleep(0.08)
print(f"cids para backtest: {len(cids)}")

def kline_at(ws, wlen, t):
    k=get(f"https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval=1s"
          f"&startTime={ws*1000}&endTime={(ws+wlen)*1000}&limit=1000")
    if not k: return None,None
    s=[(int(c[0])//1000,float(c[4])) for c in k]
    if len(s)<10: return None,None
    o=s[0][1]
    best=None;bd=9999
    for tt,p in s:
        if abs(tt-(ws+t))<bd: bd=abs(tt-(ws+t)); best=p
    return o, (best if bd<20 else None)

def winner_of(cid):
    d=get(f"https://clob.polymarket.com/markets/{cid}")
    if not d: return None
    for t in d.get("tokens",[]):
        if t.get("winner") is True or float(t.get("price") or 0)>=0.95: return t.get("outcome")
    return None

def entry_price(cid, ws, side):
    """precio del lado 'side' en un trade cercano a T_ENTRY (proxy del ask)."""
    tr=get(f"https://data-api.polymarket.com/trades?market={cid}&limit=500")
    if not isinstance(tr,list): return None
    best=None;bd=9999
    for t in tr:
        if t.get("outcome")!=side: continue
        dt=abs(int(t.get("timestamp") or 0)-(ws+T_ENTRY))
        if dt<bd: bd=dt; best=float(t.get("price") or 0)
    return best if bd<90 and best and 0.15<best<0.6 else None

bets=[]
for i,(cid,(ws,wlen)) in enumerate(cids.items()):
    if len(bets)>=200: break
    o,e = kline_at(ws,wlen,T_ENTRY); time.sleep(0.05)
    if o is None or e is None: continue
    spike=e-o
    if not (SPIKE_LO<=abs(spike)<=SPIKE_HI): continue
    faded="Down" if spike>0 else "Up"     # comprar el lado que BTC dejó atrás
    win=winner_of(cid); time.sleep(0.04)
    if win not in ("Up","Down"): continue
    price=entry_price(cid,ws,faded); time.sleep(0.05)
    if price is None: continue
    won = (faded==win)
    pe = price+COST
    pnl = (1/pe - 1) if won else -1
    bets.append({"ws":ws,"won":won,"pnl":pnl,"price":price})
    if len(bets)%25==0: print(f"  {len(bets)} apuestas simuladas…")

n=len(bets)
print(f"\n{'='*56}\n  BACKTEST FADE-SPIKE  (regla de los ganadores)\n{'='*56}")
if n:
    wr=sum(b['won'] for b in bets)/n
    ev=sum(b['pnl'] for b in bets)/n
    ap=statistics.mean(b['price'] for b in bets)
    print(f"  apuestas: {n} | win {wr:.0%} | precio medio {ap:.3f} | EV neto/apuesta {ev:+.1%}")
    bets.sort(key=lambda b:b['ws']); h=n//2
    for lab,seg in [("TRAIN (antiguo)",bets[:h]),("TEST (reciente)",bets[h:])]:
        if seg:
            w2=sum(b['won'] for b in seg)/len(seg); e2=sum(b['pnl'] for b in seg)/len(seg)
            print(f"    {lab}: n={len(seg)} | win {w2:.0%} | EV {e2:+.1%}")
    print(f"\n  VEREDICTO: EV neto {'POSITIVO' if ev>0 else 'NEGATIVO'} "
          f"({'replica el edge' if ev>0.01 else 'NO replica — cae a ~break-even/negativo como antes'})")
