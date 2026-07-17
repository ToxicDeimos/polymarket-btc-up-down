"""
LAB — análisis de la firma de fill de los ganadores, con evidencia de LIBRO.

Une fills_*.csv (ganadores) con books_*.csv y spot_*.csv del colector y responde:
  · MAKER vs TAKER con libro FRESCO (por defecto <=12s antes del fill; el colector sondea a 5s).
    BUY  p>=mejor_ask -> cruzó = TAKER ; BUY p<=mejor_bid -> orden pasiva golpeada = MAKER.
  · agresividad = cuánto cruzan el toque (¢ por encima del ask en BUY / por debajo del bid en SELL).
  · zona de precio de entrada (favorito/coinflip/longshot) — ¿qué compran?
  · fase de la ventana, spread al llenarse, momentum del spot 30s antes.
  · co-trading entre wallets (mismas ventanas en <30s) — ¿mismo operador/señal?

    python lab_analyze.py [--age S] [YYYYMMDD ...]     # --age = frescura máx del libro (def 12s)
Escribe fills_enriched.csv en research/lab/.
"""
import csv, os, sys, glob, bisect, statistics
from collections import defaultdict

DIR=os.path.join(os.path.dirname(__file__),"lab")

def days_available():
    return sorted({os.path.basename(p).split("_")[1][:8] for p in glob.glob(os.path.join(DIR,"fills_*.csv"))})

def load(name, days):
    rows=[]
    for d in days:
        p=os.path.join(DIR,f"{name}_{d}.csv")
        if os.path.exists(p): rows += list(csv.DictReader(open(p,encoding="utf-8")))
    return rows

def main():
    args=sys.argv[1:]
    MAXAGE=12
    if "--age" in args: MAXAGE=int(args[args.index("--age")+1]); del args[args.index("--age"):args.index("--age")+2]
    days = args or days_available()
    if not days: print("sin datos aún — deja correr el colector"); return
    print(f"días: {', '.join(days)}  | libro fresco <= {MAXAGE}s")
    fills=load("fills",days); books=load("books",days); spot=load("spot",days)
    print(f"fills ganadores: {len(fills)} | snapshots libro: {len(books)} | spot: {len(spot)}")

    bidx={}
    for b in books:
        try: ts=int(b["ts"]); b1=float(b["b1"]) if b["b1"] else None; a1=float(b["a1"]) if b["a1"] else None
        except Exception: continue
        bidx.setdefault((b["cid"],b["side"]),[]).append((ts,b1,a1))
    for k in bidx: bidx[k].sort()
    sidx=sorted((int(s["ts"]),float(s["price"])) for s in spot if s.get("price")); sts=[x[0] for x in sidx]

    def book_before(cid,side,t):
        arr=bidx.get((cid,side))
        if not arr: return None
        ii=bisect.bisect_right([x[0] for x in arr], t)-1
        if ii<0 or t-arr[ii][0]>MAXAGE: return None
        return arr[ii]+(t-arr[ii][0],)   # (ts,b1,a1,age)
    def spot_at(t):
        i=bisect.bisect_right(sts,t)-1
        return sidx[i][1] if i>=0 and t-sidx[i][0]<=30 else None

    seen=set(); E=[]
    for f in fills:
        key=(f.get("tx"),f.get("ts_trade"),f.get("price"),f.get("outcome"))
        if key in seen: continue
        seen.add(key)
        try: t=int(f["ts_trade"]); p=float(f["price"]); ws=int(f["slug"].split("-")[-1])
        except Exception: continue
        bk=book_before(f["cid"], f["outcome"], t)
        cls="sin_libro"; spread=None; cross=None; age=None
        if bk:
            _,b1,a1,age=bk
            if b1 is not None and a1 is not None: spread=round(a1-b1,3)
            buy=(f.get("trade_side")=="BUY")
            if buy and a1 is not None: cross=round(p-a1,3)
            elif (not buy) and b1 is not None: cross=round(b1-p,3)
            if buy:
                if a1 is not None and p>=a1-0.001: cls="TAKER"
                elif b1 is not None and p<=b1+0.001: cls="MAKER"
                else: cls="entre"
            else:
                if b1 is not None and p<=b1+0.001: cls="TAKER"
                elif a1 is not None and p>=a1-0.001: cls="MAKER"
                else: cls="entre"
        s0=spot_at(t); s30=spot_at(t-30)
        E.append({"wallet":f["wallet"],"ts":t,"phase":t-ws,"slug":f["slug"],"cid":f["cid"],
                  "side":f.get("trade_side"),"outcome":f.get("outcome"),"price":p,"cls":cls,
                  "spread":spread,"cross":cross,"age":age,"dspot30":round(s0-s30,1) if (s0 and s30) else None})
    if not E: print("sin fills."); return
    with open(os.path.join(DIR,"fills_enriched.csv"),"w",newline="",encoding="utf-8") as fo:
        w=csv.DictWriter(fo,fieldnames=list(E[0].keys())); w.writeheader(); w.writerows(E)

    print(f"\n=== MAKER/TAKER (solo fills con libro fresco <= {MAXAGE}s) ===")
    print(f"{'wallet':>12} {'n_cov':>6} {'MAKER':>6} {'TAKER':>6} {'entre':>6} {'cross_med¢':>10} {'age_med':>7} {'fase_med':>8}")
    for wl in sorted({e['wallet'] for e in E}):
        cov=[e for e in E if e["wallet"]==wl and e["cls"]!="sin_libro"]
        if not cov: print(f"{wl:>12} {'0':>6}  (sin cobertura de libro aún)"); continue
        def pct(c): return f"{sum(1 for e in cov if e['cls']==c)/len(cov)*100:.0f}%"
        cr=[e["cross"] for e in cov if e["cross"] is not None]
        crm=f"{statistics.median(cr)*100:+.1f}" if cr else "—"
        agem=f"{statistics.median(e['age'] for e in cov):.0f}s"
        fasem=f"{statistics.median(e['phase'] for e in cov):.0f}s"
        print(f"{wl:>12} {len(cov):>6} {pct('MAKER'):>6} {pct('TAKER'):>6} {pct('entre'):>6} {crm:>10} {agem:>7} {fasem:>8}")

    print(f"\n=== ZONA de precio de entrada (BUY) — ¿qué compran? ===")
    zones=[(0,0.2,"longshot<20¢"),(0.2,0.4,"20-40¢"),(0.4,0.6,"coinflip"),(0.6,0.8,"60-80¢"),(0.8,1.01,"fav>80¢")]
    print(f"{'wallet':>12} " + " ".join(f"{z[2]:>12}" for z in zones))
    for wl in sorted({e['wallet'] for e in E}):
        B=[e for e in E if e["wallet"]==wl and e["side"]=="BUY"]
        if not B: continue
        cells=[f"{sum(1 for e in B if lo<=e['price']<hi)/len(B)*100:>11.0f}%" for lo,hi,_ in zones]
        print(f"{wl:>12} " + " ".join(cells))

    print(f"\n=== CO-TRADING (¿operan las mismas ventanas casi a la vez?) ===")
    bywin=defaultdict(lambda:defaultdict(list))   # cid -> wallet -> [ts]
    for e in E: bywin[e["cid"]][e["wallet"]].append(e["ts"])
    pairs=defaultdict(int); pair_close=defaultdict(int)
    for cid,ww in bywin.items():
        wls=list(ww)
        for i in range(len(wls)):
            for j in range(i+1,len(wls)):
                a,b=sorted((wls[i],wls[j])); pairs[(a,b)]+=1
                mind=min(abs(x-y) for x in ww[wls[i]] for y in ww[wls[j]])
                if mind<=30: pair_close[(a,b)]+=1
    for (a,b),n in sorted(pairs.items(),key=lambda x:-x[1]):
        print(f"   {a} & {b}: {n} ventanas compartidas, {pair_close[(a,b)]} con fills a <=30s")

if __name__=="__main__":
    try: sys.stdout.reconfigure(encoding="utf-8")
    except Exception: pass
    main()
