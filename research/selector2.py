"""
Refinar la señal de izzyaussie/13mm-wrench: ¿su FADE captura reversiones de spikes de
spot (mean-reversion), y sus FOLLOW son cerca del cierre (confirmación/velocidad)?
Usa Binance a segundo. spot@open, spot@entry, spot@close por ventana.
"""
import urllib.request, json, sys, time, csv, os, statistics
from collections import defaultdict
try: sys.stdout.reconfigure(encoding="utf-8")
except: pass
D = os.path.dirname(__file__)

def get(url, tries=3):
    for i in range(tries):
        try:
            req=urllib.request.Request(url, headers={"User-Agent":"r/0.9"})
            with urllib.request.urlopen(req, timeout=25) as r: return json.load(r)
        except Exception:
            if i==tries-1: return None
            time.sleep(0.4*(i+1))

addr={}
for r in csv.DictReader(open(os.path.join(D,"btc_trades.csv"),encoding="utf-8")):
    if r.get("name") in ("izzyaussie","13mm-wrench"): addr[r["name"]]=r["wallet"]

def kseries(ws,wlen):
    k=get(f"https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval=1s"
          f"&startTime={ws*1000}&endTime={(ws+wlen)*1000}&limit=1000")
    return [(int(c[0])//1000,float(c[4])) for c in k] if k else []
def at(s,t):
    best=None;bd=9999
    for tt,p in s:
        if abs(tt-t)<bd: bd=abs(tt-t);best=p
    return best if bd<15 else None

WLEN={"15m":900,"5m":300,"?":900}
for name,w in addr.items():
    trades=[];off=0
    while off<=2000:
        tr=get(f"https://data-api.polymarket.com/trades?user={w}&limit=500&offset={off}")
        if not isinstance(tr,list) or not tr: break
        for t in tr:
            slug=t.get("slug","") or ""
            if "btc-updown" not in slug: continue
            var="15m" if "-15m-" in slug else ("5m" if "-5m-" in slug else "?")
            try: ws=int(slug.split("-")[-1])
            except: ws=0
            trades.append((t.get("conditionId"),t.get("outcome"),t.get("side"),
                           float(t.get("size") or 0),int(t.get("timestamp") or 0),ws,var))
        if len(tr)<500: break
        off+=500;time.sleep(0.1)
    byw=defaultdict(lambda:{"Up":0.0,"Down":0.0,"ts":9e18,"ws":0,"var":"?"})
    for cid,o,side,size,ts,ws,var in trades:
        m=byw[cid];m["ws"]=ws;m["var"]=var
        m[o]+= size if side=="BUY" else -size; m["ts"]=min(m["ts"],ts)
    wins=sorted(byw.items(),key=lambda kv:-kv[1]["ws"])[:55]
    fade={"n":0,"won":0,"rev":0,"spike":[]}; foll={"n":0,"won":0,"secs":[],"spike":[]}
    for cid,m in wins:
        d=get(f"https://clob.polymarket.com/markets/{cid}");time.sleep(0.04)
        win=None
        if d:
            for t in d.get("tokens",[]):
                if t.get("winner") is True or float(t.get("price") or 0)>=0.95: win=t.get("outcome");break
        if win not in ("Up","Down"): continue
        wlen=WLEN.get(m["var"],900); s=kseries(m["ws"],wlen);time.sleep(0.04)
        if len(s)<10: continue
        o=s[0][1]; e=at(s,m["ts"]); c=s[-1][1]
        if e is None: continue
        side="Up" if m["Up"]-m["Down"]>0 else "Down"
        emove=e-o; cmove=c-o
        follows = (side=="Up" and emove>0) or (side=="Down" and emove<0)
        won=(side==win); secs=m["ts"]-m["ws"]
        if follows:
            foll["n"]+=1; foll["won"]+=won; foll["secs"].append(secs); foll["spike"].append(abs(emove))
        else:
            fade["n"]+=1; fade["won"]+=won; fade["spike"].append(abs(emove))
            # ¿revirtió el spike? el cierre volvió hacia su lado (contra el emove)
            if (emove>0 and cmove<emove) or (emove<0 and cmove>emove): fade["rev"]+=1
    print(f"\n{'='*60}\n  {name}\n{'='*60}")
    if fade["n"]:
        print(f"  FADE (compra contra el spike):  {fade['n']} ventanas")
        print(f"    win rate: {fade['won']/fade['n']:.0%}")
        print(f"    el spike REVIRTIÓ (cierre volvió a su lado): {fade['rev']}/{fade['n']} = {fade['rev']/fade['n']:.0%}")
        print(f"    tamaño medio del spike al entrar: ${statistics.mean(fade['spike']):.0f}")
    if foll["n"]:
        print(f"  FOLLOW (compra a favor del spike): {foll['n']} ventanas")
        print(f"    win rate: {foll['won']/foll['n']:.0%}")
        print(f"    timing medio: {statistics.median(foll['secs']):.0f}s en ventana | spike ${statistics.mean(foll['spike']):.0f}")
