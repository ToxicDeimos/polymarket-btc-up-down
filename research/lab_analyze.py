"""
LAB — análisis Q1-Q3: la firma de fill de los ganadores, POR FIN con contexto de libro.

Une fills_*.csv (ganadores) con books_*.csv y spot_*.csv del colector:
  - clasifica cada fill como MAKER o TAKER con evidencia del libro (último snapshot <=60s antes):
      BUY  a precio <= mejor bid  -> su orden pasiva fue golpeada = MAKER
      BUY  a precio >= mejor ask  -> cruzó el spread = TAKER
      SELL simétrico
  - fase de la ventana (s desde el inicio), spread vigente, movimiento del spot 15/30/60s antes
  - agrega por wallet: %maker/%taker, mediana de fase, spread al llenarse, velocidad del spot

    python lab_analyze.py [YYYYMMDD ...]     # por defecto: todos los días con datos
Escribe fills_enriched.csv en research/lab/ para análisis más finos.
"""
import csv, os, sys, glob, bisect, statistics

DIR=os.path.join(os.path.dirname(__file__),"lab")

def days_available():
    return sorted({os.path.basename(p).split("_")[1][:8] for p in glob.glob(os.path.join(DIR,"fills_*.csv"))})

def load(name, days):
    rows=[]
    for d in days:
        p=os.path.join(DIR,f"{name}_{d}.csv")
        if os.path.exists(p):
            rows += list(csv.DictReader(open(p,encoding="utf-8")))
    return rows

def main():
    days = sys.argv[1:] or days_available()
    if not days: print("sin datos aún — deja correr el colector"); return
    print(f"días: {', '.join(days)}")
    fills=load("fills",days); books=load("books",days); spot=load("spot",days)
    print(f"fills ganadores: {len(fills)} | snapshots libro: {len(books)} | spot: {len(spot)}")

    # índices: (cid,side) -> [(ts, b1, a1, spread)]
    bidx={}
    for b in books:
        try: ts=int(b["ts"]); b1=float(b["b1"]) if b["b1"] else None; a1=float(b["a1"]) if b["a1"] else None
        except Exception: continue
        bidx.setdefault((b["cid"],b["side"]),[]).append((ts,b1,a1))
    for k in bidx: bidx[k].sort()
    sidx=sorted((int(s["ts"]),float(s["price"])) for s in spot if s.get("price"))
    sts=[x[0] for x in sidx]

    def book_before(cid,side,t,maxage=60):
        arr=bidx.get((cid,side))
        if not arr: return None
        i=bisect.bisect_right([x[0] for x in arr], t)-1
        if i<0 or t-arr[i][0]>maxage: return None
        return arr[i]
    def spot_at(t):
        i=bisect.bisect_right(sts,t)-1
        return sidx[i][1] if i>=0 and t-sidx[i][0]<=30 else None

    seen=set(); enriched=[]
    for f in fills:
        key=(f.get("tx"),f.get("ts_trade"),f.get("price"),f.get("outcome"))
        if key in seen: continue          # dedupe (reinicios del colector)
        seen.add(key)
        try:
            t=int(f["ts_trade"]); p=float(f["price"])
            ws=int(f["slug"].split("-")[-1])
        except Exception: continue
        bk=book_before(f["cid"], f["outcome"], t)
        cls="sin_libro"; spread=None
        if bk:
            _,b1,a1=bk
            if b1 is not None and a1 is not None: spread=round(a1-b1,3)
            buy=(f.get("trade_side")=="BUY")
            if buy:
                if a1 is not None and p>=a1-0.001: cls="TAKER"
                elif b1 is not None and p<=b1+0.001: cls="MAKER"
                else: cls="entre"
            else:
                if b1 is not None and p<=b1+0.001: cls="TAKER"
                elif a1 is not None and p>=a1-0.001: cls="MAKER"
                else: cls="entre"
        s0=spot_at(t); s30=spot_at(t-30)
        dspot30=round(s0-s30,1) if (s0 and s30) else None
        enriched.append({"wallet":f["wallet"],"ts":t,"phase":t-ws,"slug":f["slug"],
                         "side":f.get("trade_side"),"outcome":f.get("outcome"),"price":p,
                         "cls":cls,"spread":spread,"dspot30":dspot30})
    if not enriched: print("sin fills aún."); return

    with open(os.path.join(DIR,"fills_enriched.csv"),"w",newline="",encoding="utf-8") as fo:
        wcsv=csv.DictWriter(fo,fieldnames=list(enriched[0].keys())); wcsv.writeheader(); wcsv.writerows(enriched)

    print(f"\n{'wallet':>12} {'n':>4} {'MAKER':>7} {'TAKER':>7} {'entre':>6} {'s/libro':>7} "
          f"{'fase_med(s)':>11} {'spread_med':>10}")
    print("-"*72)
    for wl in sorted({e['wallet'] for e in enriched}):
        E=[e for e in enriched if e["wallet"]==wl]
        n=len(E); cov=[e for e in E if e["cls"]!="sin_libro"]
        def pct(c): return f"{sum(1 for e in cov if e['cls']==c)/len(cov)*100:.0f}%" if cov else "—"
        ph=statistics.median(e["phase"] for e in E)
        sp=[e["spread"] for e in E if e["spread"] is not None]
        spm=f"{statistics.median(sp):.3f}" if sp else "—"
        print(f"{wl:>12} {n:>4} {pct('MAKER'):>7} {pct('TAKER'):>7} {pct('entre'):>6} "
              f"{sum(1 for e in E if e['cls']=='sin_libro'):>7} {ph:>11.0f} {spm:>10}")
    ncov=sum(1 for e in enriched if e["cls"]!="sin_libro")
    print(f"\ncobertura de libro: {ncov}/{len(enriched)} fills ({ncov/len(enriched)*100:.0f}%)")
    print("enriquecido -> lab/fills_enriched.csv (para cortes más finos: fase, dspot30, precio...)")

if __name__=="__main__":
    try: sys.stdout.reconfigure(encoding="utf-8")
    except Exception: pass
    main()
