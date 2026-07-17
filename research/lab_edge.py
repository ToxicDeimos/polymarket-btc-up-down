"""
LAB experimento #2 — ¿los fills de los ganadores GANAN por encima del precio, y en qué ESTADO?

La pregunta decisiva: su entrada de taker direccional, ¿es +EV? ¿en qué estado del mercado?
Para cada fill (BUY) de los ganadores:
  · resuelve la ventana por Binance (Up gana si spot(ws+wlen) > spot(ws))
  · won = el lado que compraron ganó
  · EV/share = win_rate − precio_medio  (compran a p; si gana vale 1)
  · estado de entrada: zona de precio, fase, y MOMENTUM = compraron A FAVOR o EN CONTRA del
    movimiento intra-ventana (spot al entrar vs spot al abrir la ventana)

    python lab_edge.py [YYYYMMDD ...]
Necesita internet (Binance). Autónomo (stdlib).
"""
import urllib.request, json, time, csv, os, sys, glob
from collections import defaultdict

DIR=os.path.join(os.path.dirname(__file__),"lab")

def get(url, tries=3):
    for i in range(tries):
        try:
            req=urllib.request.Request(url, headers={"User-Agent":"labedge/1"})
            with urllib.request.urlopen(req, timeout=15) as r: return json.load(r)
        except Exception:
            if i==tries-1: return None
            time.sleep(0.4)

_sc={}
def spot_at(ts):
    if ts in _sc: return _sc[ts]
    k=get(f"https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval=1s&startTime={ts*1000}&endTime={(ts+2)*1000}&limit=1")
    _sc[ts]=float(k[0][4]) if k else None
    return _sc[ts]

def load_fills(days):
    rows=[]
    for d in days:
        p=os.path.join(DIR,f"fills_{d}.csv")
        if os.path.exists(p): rows += list(csv.DictReader(open(p,encoding="utf-8")))
    return rows

def stats(recs):
    n=len(recs)
    if not n: return None
    wr=sum(r["won"] for r in recs)/n
    ap=sum(r["price"] for r in recs)/n
    return {"n":n,"win":wr,"price":ap,"ev":wr-ap}

def line(label, s):
    if not s: print(f"  {label:>22}  (sin datos)"); return
    print(f"  {label:>22}  n={s['n']:>4}  win {s['win']:.1%}  precio {s['price']:.1%}  EV/share {s['ev']*100:+.1f}¢")

def main():
    days=sys.argv[1:] or sorted({os.path.basename(p).split('_')[1][:8] for p in glob.glob(os.path.join(DIR,'fills_*.csv'))})
    if not days: print("sin datos"); return
    fills=load_fills(days)
    print(f"días: {', '.join(days)}  | fills brutos: {len(fills)}  (resolviendo por Binance...)")

    seen=set(); R=[]
    for f in fills:
        if f.get("trade_side")!="BUY": continue
        key=(f.get("tx"),f.get("ts_trade"),f.get("price"),f.get("outcome"))
        if key in seen: continue
        seen.add(key)
        try:
            t=int(f["ts_trade"]); p=float(f["price"]); slug=f["slug"]; ws=int(slug.split("-")[-1])
            wlen=900 if "-15m-" in slug else 300
        except Exception: continue
        if ws+wlen > int(time.time())-2: continue          # ventana no cerrada
        o=spot_at(ws); c=spot_at(ws+wlen); e=spot_at(t)
        if o is None or c is None: continue
        winner="Up" if c>o else "Down"
        won=1 if f.get("outcome")==winner else 0
        intra = (e-o) if e is not None else None            # movimiento intra-ventana al entrar
        # ¿compraron a favor o en contra del movimiento?
        withmom=None
        if intra is not None and abs(intra)>1:
            up_side=(f.get("outcome")=="Up")
            withmom = (up_side and intra>0) or ((not up_side) and intra<0)
        R.append({"wallet":f["wallet"],"price":p,"won":won,"mkt":"15m" if wlen==900 else "5m",
                  "phase":t-ws,"withmom":withmom,"intra":intra})
        time.sleep(0.02)
    if not R: print("sin fills resueltos."); return

    print(f"fills resueltos: {len(R)}\n")
    print("=== GLOBAL (todos los ganadores juntos) ===")
    line("TODO", stats(R))
    print("\n=== por wallet ===")
    for wl in sorted({r['wallet'] for r in R}): line(wl, stats([r for r in R if r['wallet']==wl]))

    print("\n=== por ZONA de precio (¿dónde ganan?) ===")
    for lo,hi,lab in [(0,0.2,"longshot<20¢"),(0.2,0.4,"20-40¢"),(0.4,0.6,"coinflip"),(0.6,0.8,"60-80¢"),(0.8,1.01,"fav>80¢")]:
        line(lab, stats([r for r in R if lo<=r["price"]<hi]))

    print("\n=== por MOMENTUM (¿a favor o contra el movimiento intra-ventana?) ===")
    line("A FAVOR (momentum)", stats([r for r in R if r["withmom"] is True]))
    line("EN CONTRA (fade)",   stats([r for r in R if r["withmom"] is False]))

    print("\n=== por MERCADO ===")
    for m in ("5m","15m"): line(m, stats([r for r in R if r["mkt"]==m]))

    print("\n=== por FASE de entrada ===")
    for lo,hi in [(0,120),(120,240),(240,600),(600,900)]:
        s=stats([r for r in R if lo<=r["phase"]<hi])
        if s: line(f"{lo}-{hi}s", s)

    with open(os.path.join(DIR,"edge_resolved.csv"),"w",newline="",encoding="utf-8") as fo:
        w=csv.DictWriter(fo,fieldnames=list(R[0].keys())); w.writeheader(); w.writerows(R)
    print("\n-> lab/edge_resolved.csv")
    g=stats(R)
    print("\nVEREDICTO:")
    if g["ev"]>0.02: print(f"  → SUS FILLS SON +EV (win {g['win']:.1%} > precio {g['price']:.1%}) — la señal predice. Reconstruible.")
    elif g["ev"]>-0.01: print(f"  → break-even — el edge no está en la señal de entrada (¿tamaño? ¿otra cosa?)")
    else: print(f"  → sus fills pierden en muestra — survivorship o edge fuera de estos trades")

if __name__=="__main__":
    try: sys.stdout.reconfigure(encoding="utf-8")
    except Exception: pass
    main()
