"""
TEST OUT-OF-SAMPLE en datos FRESCOS de la regla pre-registrada:
  |spike|<=8 AND vol_pre<21.1  -> comprar el lado barato (0.20-0.49) a 180s.
Excluye las ventanas ya usadas (por ws). Ventanas nuevas = confirmación honesta.
"""
import urllib.request, json, sys, time, csv, os, math, statistics
from collections import Counter
try: sys.stdout.reconfigure(encoding="utf-8")
except: pass
D=os.path.dirname(__file__); T=180; SPIKE_MAX=8; VOL_MAX=21.1; COST=0.01

def get(url,tries=3):
    for i in range(tries):
        try:
            req=urllib.request.Request(url,headers={"User-Agent":"ft/1.0"})
            with urllib.request.urlopen(req,timeout=25) as r: return json.load(r)
        except Exception:
            if i==tries-1: return None
            time.sleep(0.35*(i+1))

# ventanas ya usadas (excluir)
used=set()
for r in csv.DictReader(open(os.path.join(D,"features.csv"),encoding="utf-8")):
    try: used.add(int(r["ws"]))
    except: pass
print(f"ventanas ya usadas a excluir: {len(used)}",flush=True)

cnt=Counter()
for r in csv.DictReader(open(os.path.join(D,"btc_trades.csv"),encoding="utf-8")): cnt[r["wallet"]]+=1
wallets=[w for w,_ in cnt.most_common(90)][30:]      # tier distinto de wallets

cids={}
for w in wallets:
    off=0
    while off<=6000:                                  # más profundo = ventanas más antiguas
        tr=get(f"https://data-api.polymarket.com/trades?user={w}&limit=500&offset={off}")
        if not isinstance(tr,list) or not tr: break
        for t in tr:
            slug=t.get("slug","") or ""
            if "btc-updown" not in slug: continue
            cid=t.get("conditionId")
            try: ws=int(slug.split("-")[-1])
            except: ws=0
            if ws in used: continue
            if cid not in cids: cids[cid]=(ws,900 if "-15m-" in slug else 300)
        if len(tr)<500: break
        off+=500;time.sleep(0.05)
print(f"cids frescos candidatos: {len(cids)}",flush=True)

def feats(ws,wlen):
    k=get(f"https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval=1s&startTime={ws*1000}&endTime={(ws+wlen)*1000}&limit=1000")
    if not k or len(k)<10: return None,None
    s=[(int(c[0])//1000,float(c[4])) for c in k];o=s[0][1]
    best=None;bd=9999
    for tt,p in s:
        if abs(tt-(ws+T))<bd: bd=abs(tt-(ws+T));best=p
    if best is None or bd>=20: return None,None
    kp=get(f"https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval=1m&startTime={(ws-1800)*1000}&endTime={ws*1000}&limit=100")
    pr=[float(c[4]) for c in kp] if kp else []
    vol=statistics.pstdev([pr[i]-pr[i-1] for i in range(1,len(pr))]) if len(pr)>5 else 999
    return best-o, vol
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

bets=[]
for cid,(ws,wlen) in cids.items():
    if len(bets)>=220: break
    sp,vol=feats(ws,wlen);time.sleep(0.03)
    if sp is None or abs(sp)>SPIKE_MAX or vol>VOL_MAX: continue   # LA REGLA
    up,dn=prices_180(cid,ws);time.sleep(0.02)
    if up is None or dn is None: continue
    cheap="Up" if up<dn else "Down";cp=min(up,dn)
    if not (0.20<=cp<=0.49): continue
    win=winner_of(cid);time.sleep(0.02)
    if win not in ("Up","Down"): continue
    bets.append((cp,1 if cheap==win else 0))
    if len(bets)%25==0: print(f"  {len(bets)} apuestas frescas…",flush=True)

n=len(bets)
print(f"\n{'='*56}\n  CONFIRMACIÓN OOS EN DATOS FRESCOS (n={n})\n{'='*56}")
if n>=30:
    wins=sum(b[1] for b in bets);wr=wins/n;ap=statistics.mean(b[0] for b in bets)
    se=math.sqrt(wr*(1-wr)/n);lo,hi=wr-1.96*se,wr+1.96*se
    ev=sum((1/(b[0]+COST)-1) if b[1] else -1 for b in bets)/n
    print(f"  WIN {wr:.1%} (IC95%: {lo:.1%}-{hi:.1%}) | break-even {ap:.1%} | EV {ev:+.1%}")
    if lo>ap: print("  → CONFIRMADO: la regla mantiene edge en datos frescos (win > precio, significativo)")
    elif wr>ap: print("  → POSITIVO pero no significativo (win > precio, IC toca break-even)")
    else: print("  → NO se confirma: el edge se desinfla en datos frescos (era sobreajuste/azar)")
else: print("  muestra insuficiente")
