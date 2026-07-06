"""
Reconstruir el SELECTOR de los ganadores "compra barato, gana ~51%".
Pregunta: al ENTRAR, ¿compran el lado hacia el que BTC (Binance) YA se movió
(siguen el movimiento real, más rápido que Polymarket reprecia = edge de velocidad),
o fadean (compran contra el movimiento)?
"""
import urllib.request, json, sys, time, csv, os, statistics
from collections import defaultdict
try: sys.stdout.reconfigure(encoding="utf-8")
except: pass
D = os.path.dirname(__file__)

def get(url, tries=3):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent":"research/0.8"})
            with urllib.request.urlopen(req, timeout=25) as r:
                return json.load(r)
        except Exception:
            if i == tries-1: return None
            time.sleep(0.4*(i+1))

# Direcciones completas por nombre (del muestreo)
addr = {}
for r in csv.DictReader(open(os.path.join(D,"btc_trades.csv"), encoding="utf-8")):
    if r.get("name") in ("izzyaussie","13mm-wrench","wwwise"):
        addr[r["name"]] = r["wallet"]
print("objetivos:", addr)

def btc_series(wstart, wlen):
    """klines 1s de Binance [wstart, wstart+wlen] -> lista (t_seg, close)."""
    url=(f"https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval=1s"
         f"&startTime={wstart*1000}&endTime={(wstart+wlen)*1000}&limit=1000")
    k=get(url)
    if not k: return []
    return [(int(c[0])//1000, float(c[4])) for c in k]

def price_at(series, ts):
    best=None; bd=9999
    for t,p in series:
        if abs(t-ts)<bd: bd=abs(t-ts); best=p
    return best if bd<10 else None

WLEN={"15m":900,"5m":300,"?":900}
report=defaultdict(lambda:{"n":0,"aligned":0,"a_win":0,"a_n":0,"f_win":0,"f_n":0})

for name, w in addr.items():
    # historial btc del wallet
    trades=[]; offset=0
    while offset<=2000:
        tr=get(f"https://data-api.polymarket.com/trades?user={w}&limit=500&offset={offset}")
        if not isinstance(tr,list) or not tr: break
        for t in tr:
            slug=t.get("slug","") or ""
            if "btc-updown" not in slug: continue
            var="15m" if "-15m-" in slug else ("5m" if "-5m-" in slug else "?")
            try: ws=int(slug.split("-")[-1])
            except: ws=0
            trades.append((t.get("conditionId"), t.get("side"), t.get("outcome"),
                           float(t.get("price") or 0), float(t.get("size") or 0),
                           int(t.get("timestamp") or 0), ws, var))
        if len(tr)<500: break
        offset+=500; time.sleep(0.1)
    # agrupar por ventana: lado neto + primera entrada
    byw=defaultdict(lambda:{"Up":0.0,"Down":0.0,"ts":9e18,"ws":0,"var":"?","pr":[]})
    for cid,side,o,price,size,ts,ws,var in trades:
        m=byw[cid]; m["ws"]=ws; m["var"]=var
        sgn = size if side=="BUY" else -size
        m[o]+=sgn; m["ts"]=min(m["ts"],ts); m["pr"].append(price)
    # limitar a ~50 ventanas recientes por wallet (coste Binance)
    wins=sorted(byw.items(), key=lambda kv:-kv[1]["ws"])[:50]
    for cid,m in wins:
        net_side = "Up" if m["Up"]-m["Down"]>0 else "Down"
        # ganador
        d=get(f"https://clob.polymarket.com/markets/{cid}"); time.sleep(0.05)
        win=None
        if d:
            for t in d.get("tokens",[]):
                pr=float(t.get("price") or 0)
                if t.get("winner") is True or pr>=0.95: win=t.get("outcome"); break
        if win not in ("Up","Down"): continue
        wlen=WLEN.get(m["var"],900)
        ser=btc_series(m["ws"], wlen); time.sleep(0.05)
        p_open=ser[0][1] if ser else None
        p_ent=price_at(ser, m["ts"])
        if p_open is None or p_ent is None: continue
        move=p_ent-p_open
        # ¿siguen el movimiento real? (compran Up si BTC subió, Down si bajó)
        aligned = (net_side=="Up" and move>0) or (net_side=="Down" and move<0)
        won = (net_side==win)
        R=report[name]; R["n"]+=1; R["aligned"]+=1 if aligned else 0
        if aligned: R["a_n"]+=1; R["a_win"]+=1 if won else 0
        else: R["f_n"]+=1; R["f_win"]+=1 if won else 0

print("\n"+"="*70)
print("  SELECTOR: ¿siguen el movimiento REAL de BTC al entrar?")
print("="*70)
for name,R in report.items():
    if not R["n"]: continue
    print(f"\n  {name}:  {R['n']} ventanas")
    print(f"    compran el lado que BTC YA movió (siguen): {R['aligned']}/{R['n']} = {R['aligned']/R['n']:.0%}")
    if R["a_n"]: print(f"      cuando SIGUEN el movimiento  → ganan {R['a_win']}/{R['a_n']} = {R['a_win']/R['a_n']:.0%}")
    if R["f_n"]: print(f"      cuando FADEAN (contra)       → ganan {R['f_win']}/{R['f_n']} = {R['f_win']/R['f_n']:.0%}")
