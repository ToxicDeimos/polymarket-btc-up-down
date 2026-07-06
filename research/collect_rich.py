"""
Colector RICO: por ventana (oportunidad de fade a 180s) guarda MUCHAS features
para aprender un selector con modelo. Objetivo ~800 ventanas. Guardado incremental.
"""
import urllib.request, json, sys, time, csv, os, statistics
from collections import Counter, defaultdict
from datetime import datetime, timezone
try: sys.stdout.reconfigure(encoding="utf-8")
except: pass
D=os.path.dirname(__file__); T=180; SPIKE_CAP=30
OUT=os.path.join(D,"rich.csv")

def get(url,tries=3):
    for i in range(tries):
        try:
            req=urllib.request.Request(url,headers={"User-Agent":"cr/1.0"})
            with urllib.request.urlopen(req,timeout=25) as r: return json.load(r)
        except Exception:
            if i==tries-1: return None
            time.sleep(0.35*(i+1))

done=set()
if os.path.exists(OUT):
    for r in csv.DictReader(open(OUT,encoding="utf-8")):
        try: done.add(int(r["ws"]))
        except: pass
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
                if ws not in done: cids[cid]=(ws,900 if "-15m-" in slug else 300)
        if len(tr)<500: break
        off+=500;time.sleep(0.05)
print(f"cids: {len(cids)}",flush=True)

def btc(ws,wlen):
    k=get(f"https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval=1s&startTime={ws*1000}&endTime={(ws+wlen)*1000}&limit=1000")
    return [(int(c[0])//1000,float(c[4])) for c in k] if k and len(k)>=10 else None
def at(s,t):
    b=None;bd=9999
    for tt,p in s:
        if abs(tt-t)<bd: bd=abs(tt-t);b=p
    return b
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
            if ws<=ts<=ws+180: vol[(o,t.get("side"))]+=sz; nt+=1; tv+=sz
        if int(tr[-1].get("timestamp") or 0)<ws: break
        off+=500;time.sleep(0.05)
    return (px,vol,nt,tv) if pd["Up"]<75 and pd["Down"]<75 else None
def winner_of(cid):
    d=get(f"https://clob.polymarket.com/markets/{cid}")
    if not d: return None
    for t in d.get("tokens",[]):
        if t.get("winner") is True or float(t.get("price") or 0)>=0.95: return t.get("outcome")
    return None

fh=open(OUT,"a",newline="",encoding="utf-8");w=csv.writer(fh)
cols=["ws","wlen","spike","abs_spike","vol_pre","peak_dev","reverting","btc_cont60",
      "cheap_price","divergence","buy_cheap","sell_cheap","buy_exp","sell_exp",
      "flow_imb","sellpress","n_trades","total_vol","hour","won"]
if not done: w.writerow(cols)
nrec=len(done)
for cid,(ws,wlen) in cids.items():
    if nrec>=800: break
    s=btc(ws,wlen);time.sleep(0.03)
    if not s: continue
    o=s[0][1]; e=at(s,ws+T); spike=e-o
    if abs(spike)>SPIKE_CAP: continue
    peak=max((abs(p-o) for tt,p in s if tt<=ws+T), default=0)
    cont=max((((at(s,ws+T+k)-e) if spike>0 else (e-at(s,ws+T+k))) for k in range(1,61) if at(s,ws+T+k) is not None), default=0)
    m=mkt(cid,ws);time.sleep(0.02)
    if not m: continue
    px,vol,nt,tv=m
    cheap="Up" if px["Up"]<px["Down"] else "Down"; exp="Down" if cheap=="Up" else "Up"; cp=px[cheap]
    if not (0.15<=cp<=0.5): continue
    vp=prewin(ws);time.sleep(0.02)
    if vp is None: continue
    win=winner_of(cid);time.sleep(0.02)
    if win not in ("Up","Down"): continue
    bc=vol[(cheap,"BUY")];sc=vol[(cheap,"SELL")];be=vol[(exp,"BUY")];se=vol[(exp,"SELL")]
    hour=datetime.fromtimestamp(ws,timezone.utc).hour
    w.writerow([ws,wlen,round(spike,1),round(abs(spike),1),round(vp,2),round(peak,1),
                1 if abs(spike)<0.6*peak else 0,round(cont,1),round(cp,3),round(abs(px["Up"]-px["Down"]),3),
                round(bc,1),round(sc,1),round(be,1),round(se,1),
                round(be/(bc+1),2),round(sc/(bc+sc+1),2),nt,round(tv,0),hour,
                1 if cheap==win else 0]);nrec+=1
    if nrec%25==0: fh.flush();print(f"  {nrec} ventanas…",flush=True)
fh.close();print(f"FIN: {nrec} ventanas -> {OUT}",flush=True)
