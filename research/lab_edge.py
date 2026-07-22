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
import urllib.request, json, time, csv, os, sys, glob, bisect
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
    cost=sum(r["size"]*r["price"] for r in recs)
    pnl =sum(r["size"]*(r["won"]-r["price"]) for r in recs)
    return {"n":n,"win":wr,"price":ap,"ev":wr-ap,"cost":cost,"pnl":pnl,
            "roi":(pnl/cost if cost else 0),"vol":sum(r["size"] for r in recs)}

def line(label, s):
    if not s: print(f"  {label:>22}  (sin datos)"); return
    print(f"  {label:>22}  n={s['n']:>4}  win {s['win']:.1%}  precio {s['price']:.1%}  "
          f"EV/share {s['ev']*100:+.1f}¢  |  ${{$}}: PnL {s['pnl']:>+8.0f} sobre ${s['cost']:>7.0f}  ROI {s['roi']*100:+.1f}%".replace("{$}",""))

def main():
    days=sys.argv[1:] or sorted({os.path.basename(p).split('_')[1][:8] for p in glob.glob(os.path.join(DIR,'fills_*.csv'))})
    if not days: print("sin datos"); return
    fills=load_fills(days)
    cl=[]
    for d in days:
        p=os.path.join(DIR,f"chainlink_{d}.csv")
        if os.path.exists(p): cl+=list(csv.DictReader(open(p,encoding="utf-8")))
    clidx=sorted((int(c["ts"]),float(c["price"])) for c in cl if c.get("price")); clts=[x[0] for x in clidx]
    def lcl(ts,maxage=60):
        i=bisect.bisect_right(clts,ts)-1
        return clidx[i][1] if i>=0 and ts-clidx[i][0]<=maxage else None
    print(f"días: {', '.join(days)}  | fills brutos: {len(fills)} | chainlink {len(clidx)}  (resuelve por CHAINLINK donde haya, Binance si no)")

    seen=set(); R=[]
    for f in fills:
        if f.get("trade_side")!="BUY": continue
        key=(f.get("tx"),f.get("ts_trade"),f.get("price"),f.get("outcome"))
        if key in seen: continue
        seen.add(key)
        try:
            t=int(f["ts_trade"]); p=float(f["price"]); slug=f["slug"]; ws=int(slug.split("-")[-1])
            sz=float(f.get("size") or 0); wlen=900 if "-15m-" in slug else 300
        except Exception: continue
        if ws+wlen > int(time.time())-2: continue          # ventana no cerrada
        o=spot_at(ws); c=spot_at(ws+wlen); e=spot_at(t)
        if o is None or c is None: continue
        # resolución REAL por CHAINLINK si hay dato (>= como el mercado), si no Binance
        clo=lcl(ws); clc=lcl(ws+wlen)
        if clo is not None and clc is not None:
            winner="Up" if clc>=clo else "Down"; rsrc="cl"
        else:
            winner="Up" if c>=o else "Down"; rsrc="bin"
        won=1 if f.get("outcome")==winner else 0
        intra = (e-o) if e is not None else None            # movimiento intra-ventana al entrar
        # ¿compraron a favor o en contra del movimiento?
        withmom=None
        if intra is not None and abs(intra)>1:
            up_side=(f.get("outcome")=="Up")
            withmom = (up_side and intra>0) or ((not up_side) and intra<0)
        R.append({"wallet":f["wallet"],"price":p,"won":won,"size":sz,"mkt":"15m" if wlen==900 else "5m",
                  "phase":t-ws,"withmom":withmom,"intra":intra,"absmove":round(abs(c-o),1),"day":slug,"rsrc":rsrc})
        time.sleep(0.02)
    if not R: print("sin fills resueltos."); return

    ncl=sum(1 for r in R if r.get("rsrc")=="cl")
    print(f"fills resueltos: {len(R)}  (por Chainlink: {ncl}, por Binance: {len(R)-ncl})\n")
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

    print("\n=== ¿EDGE o RÉGIMEN? momentum (A FAVOR) según la FUERZA del movimiento de la ventana ===")
    print("  (si gana solo en ventanas FUERTES = trend-following/régimen; si gana en SUAVES = edge real)")
    fav=[r for r in R if r["withmom"] is True]
    for lo,hi,lab in [(0,15,"suave <$15"),(15,40,"media $15-40"),(40,1e9,"fuerte >$40")]:
        line(lab, stats([r for r in fav if lo<=r["absmove"]<hi]))

    print("\n=== por DÍA (¿aguanta cada día, o fue uno bueno?) ===")
    bydays=sorted({r["day"][r["day"].rfind('-')+1:] for r in R})
    # agrupa por fecha real de la ventana (unix -> día)
    import datetime as _dt
    def dstr(r): return _dt.datetime.utcfromtimestamp(int(r["day"].split("-")[-1])).strftime("%m-%d")
    for d in sorted({dstr(r) for r in R}):
        line(d, stats([r for r in R if dstr(r)==d]))

    with open(os.path.join(DIR,"edge_resolved.csv"),"w",newline="",encoding="utf-8") as fo:
        w=csv.DictWriter(fo,fieldnames=list(R[0].keys())); w.writeheader(); w.writerows(R)
    print("\n-> lab/edge_resolved.csv")
    g=stats(R)
    print("\nVEREDICTO (ponderado por DINERO, que es lo que cuenta):")
    print(f"  PnL total {g['pnl']:+.0f} sobre ${g['cost']:.0f} desplegado  →  ROI {g['roi']*100:+.1f}%")
    if g["roi"]>0.02:   print("  → GANAN EN DINERO aunque por-fill sea break-even: apuestan GRANDE donde ganan. Edge real.")
    elif g["roi"]>-0.01:print("  → break-even también en dinero — el edge no está en estos trades (¿regímenes? ¿rebates?)")
    else:               print("  → pierden también en dinero en esta muestra — survivorship o muestra corta/mal régimen")

if __name__=="__main__":
    try: sys.stdout.reconfigure(encoding="utf-8")
    except Exception: pass
    main()
