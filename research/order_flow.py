"""
¿La selección de izzyaussie es ORDER-FLOW? Hipótesis: fadea el lado barato cuando
lo VENDEN con pánico (presión vendedora alta) → da liquidez → revierte.
Mide en [0,180s] el flujo por lado y cruza con su win rate.
"""
import urllib.request, json, sys, time, csv, os, statistics
from collections import defaultdict
try: sys.stdout.reconfigure(encoding="utf-8")
except: pass
D=os.path.dirname(__file__)

def get(url,tries=3):
    for i in range(tries):
        try:
            req=urllib.request.Request(url,headers={"User-Agent":"of/1.0"})
            with urllib.request.urlopen(req,timeout=25) as r: return json.load(r)
        except Exception:
            if i==tries-1: return None
            time.sleep(0.35*(i+1))

addr={}
for r in csv.DictReader(open(os.path.join(D,"btc_trades.csv"),encoding="utf-8")):
    if r.get("name") in ("izzyaussie","13mm-wrench"): addr[r["name"]]=r["wallet"]

def winner_of(cid):
    d=get(f"https://clob.polymarket.com/markets/{cid}")
    if not d: return None
    for t in d.get("tokens",[]):
        if t.get("winner") is True or float(t.get("price") or 0)>=0.95: return t.get("outcome")
    return None

def flow_0_180(cid, ws):
    """volumen buy/sell por outcome en [ws, ws+180]."""
    vol=defaultdict(float); off=0
    for _ in range(8):
        tr=get(f"https://data-api.polymarket.com/trades?market={cid}&limit=500&offset={off}")
        if not isinstance(tr,list) or not tr: break
        for t in tr:
            ts=int(t.get("timestamp") or 0)
            if ws<=ts<=ws+180:
                vol[(t.get("outcome"),t.get("side"))]+=float(t.get("size") or 0)
        if int(tr[-1].get("timestamp") or 0)<ws: break
        off+=500; time.sleep(0.05)
    return vol

recs=[]
for name,w in addr.items():
    trades=[];off=0
    while off<=2000:
        tr=get(f"https://data-api.polymarket.com/trades?user={w}&limit=500&offset={off}")
        if not isinstance(tr,list) or not tr: break
        for t in tr:
            slug=t.get("slug","") or ""
            if "btc-updown" not in slug: continue
            try: ws=int(slug.split("-")[-1])
            except: ws=0
            trades.append((t.get("conditionId"),t.get("outcome"),t.get("side"),float(t.get("size") or 0),ws))
        if len(tr)<500: break
        off+=500;time.sleep=getattr(time,"sleep");time.sleep(0.08)
    byw=defaultdict(lambda:{"Up":0.0,"Down":0.0,"ws":0})
    for cid,o,side,size,ws in trades:
        m=byw[cid];m["ws"]=ws; m[o]+= size if side=="BUY" else -size
    for cid,m in sorted(byw.items(),key=lambda kv:-kv[1]["ws"])[:55]:
        win=winner_of(cid);time.sleep(0.03)
        if win not in ("Up","Down"): continue
        bet="Up" if m["Up"]-m["Down"]>0 else "Down"; exp="Down" if bet=="Up" else "Up"
        f=flow_0_180(cid,m["ws"]);time.sleep(0.03)
        buy_cheap=f[(bet,"BUY")]; sell_cheap=f[(bet,"SELL")]
        buy_exp=f[(exp,"BUY")]
        sp = sell_cheap/(buy_cheap+sell_cheap+1e-9)          # presión vendedora sobre su lado
        imb = buy_exp/(buy_cheap+1e-9)                        # gente pilando el favorito vs su lado
        recs.append({"won":bet==win,"sp":sp,"imb":imb,"sell_cheap":sell_cheap})

n=len(recs)
print(f"\n{'='*60}\n  ORDER-FLOW COMO SELECCIÓN (n={n})\n{'='*60}")
def wr(s): return f"{sum(x['won'] for x in s)/len(s):.0%} (n={len(s)})" if s else "-"
print(f"  win rate global: {wr(recs)}\n")
spm=statistics.median([x['sp'] for x in recs]); imbm=statistics.median([x['imb'] for x in recs])
print(f"  Presión vendedora sobre SU lado (mediana {spm:.2f}):")
print(f"    ALTA (venden su lado con fuerza): {wr([x for x in recs if x['sp']>spm])}")
print(f"    BAJA:                             {wr([x for x in recs if x['sp']<=spm])}")
print(f"\n  Desequilibrio: compran el FAVORITO >> su lado (mediana {imbm:.1f}):")
print(f"    ALTO (overreacción al favorito):  {wr([x for x in recs if x['imb']>imbm])}")
print(f"    BAJO:                             {wr([x for x in recs if x['imb']<=imbm])}")
