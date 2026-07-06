"""
Recoge ventanas de spike diminuto con FEATURES RICAS para buscar filtros extra
(disciplina: luego se explora en TRAIN y se confirma en TEST).
Features: spike, precio barato, divergencia de precio, vol previa, si el spike revierte ya.
"""
import urllib.request, json, sys, time, csv, os, math, statistics
from collections import Counter
try: sys.stdout.reconfigure(encoding="utf-8")
except: pass
D=os.path.dirname(__file__); T=180; SPIKE_MAX=10
OUT=os.path.join(D,"features.csv")

def get(url,tries=3):
    for i in range(tries):
        try:
            req=urllib.request.Request(url,headers={"User-Agent":"cf/1.0"})
            with urllib.request.urlopen(req,timeout=25) as r: return json.load(r)
        except Exception:
            if i==tries-1: return None
            time.sleep(0.35*(i+1))

cnt=Counter()
for r in csv.DictReader(open(os.path.join(D,"btc_trades.csv"),encoding="utf-8")): cnt[r["wallet"]]+=1
cids={}
for w,_ in cnt.most_common(40):
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
        off+=500;time.sleep(0.05)
print(f"cids: {len(cids)}",flush=True)

def inwin(ws,wlen):
    k=get(f"https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval=1s&startTime={ws*1000}&endTime={(ws+wlen)*1000}&limit=1000")
    return [(int(c[0])//1000,float(c[4])) for c in k] if k else []
def prewin(ws):
    k=get(f"https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval=1m&startTime={(ws-1800)*1000}&endTime={ws*1000}&limit=100")
    return [float(c[4]) for c in k] if k else []
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

fh=open(OUT,"w",newline="",encoding="utf-8");w=csv.writer(fh)
w.writerow(["ws","wlen","spike","cheap","cheap_price","divergence","vol_pre","reverting","won"])
nrec=0
for cid,(ws,wlen) in cids.items():
    if nrec>=500: break
    s=inwin(ws,wlen);time.sleep(0.03)
    if len(s)<10: continue
    o=s[0][1]; e=None;bd=9999;peak=0
    for tt,p in s:
        if tt<=ws+T and abs(p-o)>peak: peak=abs(p-o)
        if abs(tt-(ws+T))<bd: bd=abs(tt-(ws+T));e=p
    if e is None or bd>=20: continue
    spike=e-o
    if abs(spike)>SPIKE_MAX: continue
    up,dn=prices_180(cid,ws);time.sleep(0.02)
    if up is None or dn is None: continue
    cheap="Up" if up<dn else "Down";cp=min(up,dn)
    if not (0.20<=cp<=0.49): continue
    win=winner_of(cid);time.sleep(0.02)
    if win not in ("Up","Down"): continue
    pre=prewin(ws);time.sleep(0.02)
    vol=statistics.pstdev([pre[i]-pre[i-1] for i in range(1,len(pre))]) if len(pre)>5 else 0
    divergence=abs(up-dn)                       # cuánto se desvió el mercado del 50/50
    reverting=1 if (peak>0 and abs(spike)<0.6*peak) else 0   # el spike ya se dio la vuelta
    won=1 if cheap==win else 0
    w.writerow([ws,wlen,round(spike,1),cheap,round(cp,3),round(divergence,3),round(vol,2),reverting,won]);nrec+=1
    if nrec%25==0: fh.flush();print(f"  {nrec} ventanas…",flush=True)
fh.close();print(f"FIN: {nrec} ventanas -> {OUT}",flush=True)
