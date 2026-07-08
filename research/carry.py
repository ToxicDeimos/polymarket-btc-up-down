"""
Edge de CARRY / descuento por bloqueo de capital: comprar el FAVORITO (precio de apertura alto)
y aguantar a resolución. ¿Los favoritos ganan MÁS que su precio (edge, mercado sobre-descuenta por
inmovilizar capital) o justo su precio (eficiente)?
Retorno realizado por $1 apostado = (1/P − 1) si gana, −1 si pierde — contando las palizas.
Sobre TODOS los tipos de mercado resueltos (sports, política, etc.), excluyendo cripto up/down
intradía (sin periodo de bloqueo real). Precio de apertura = media de los primeros K trades.

    python carry.py [max_markets_procesados]
Autónomo (stdlib). obs en carry_obs.csv (gitignored).
"""
import urllib.request, json, time, ast, csv, os, sys, math

K = 8
OBS = os.path.join(os.path.dirname(__file__),"carry_obs.csv")
INTRA = ("-5m-","-15m-","-1h-","-4h-")

def get(url, tries=2):
    for i in range(tries):
        try:
            req=urllib.request.Request(url, headers={"User-Agent":"carry/1"})
            with urllib.request.urlopen(req, timeout=25) as r: return json.load(r)
        except Exception:
            if i==tries-1: return None
            time.sleep(0.4)

def opening(cid, out0):
    tr=get(f"https://data-api.polymarket.com/trades?market={cid}&limit=500")
    if not isinstance(tr,list) or not tr or len(tr)>=500: return None   # sin trades o truncado
    tr2=sorted(tr, key=lambda t:int(t.get("timestamp") or 0))[:K]
    acc=[]
    for t in tr2:
        try: p=float(t.get("price"))
        except Exception: continue
        acc.append(p if t.get("outcome")==out0 else 1-p)
    if len(acc)<3: return None
    op=sum(acc)/len(acc)
    return op if 0.0<op<1.0 else None

def collect(maxm):
    recs=[]; off=0; processed=0
    while processed<maxm and off<7000:
        d=get(f"https://gamma-api.polymarket.com/markets?closed=true&limit=100&order=id&ascending=false&offset={off}")
        if not isinstance(d,list) or not d: break
        for m in d:
            slug=m.get("slug","") or ""
            if "-updown-" in slug and any(c in slug for c in INTRA): continue   # cripto intradía
            try:
                outs=ast.literal_eval(m["outcomes"]); pr=[float(x) for x in ast.literal_eval(m["outcomePrices"])]
            except Exception: continue
            if len(outs)!=2 or sorted(pr)!=[0.0,1.0]: continue
            winner=outs[0] if pr[0]>0.5 else outs[1]
            op0=opening(m.get("conditionId"), outs[0])
            processed+=1
            if op0 is not None:
                if op0>=0.5: pf, won = op0, (outs[0]==winner)
                else:        pf, won = 1-op0, (outs[1]==winner)
                recs.append((round(pf,4), 1 if won else 0, slug.split("-")[0]))
            if processed>=maxm: break
        off+=100; time.sleep(0.04)
    print(f"mercados procesados: {processed}  →  favoritos con apertura: {len(recs)}\n")
    with open(OBS,"w",newline="",encoding="utf-8") as f:
        w=csv.writer(f); w.writerow(["fav_open_price","won","cat"]); w.writerows(recs)
    return recs

def report(recs):
    bins=[(0.5,0.6),(0.6,0.7),(0.7,0.8),(0.8,0.9),(0.9,0.95),(0.95,1.01)]
    print(f"{'bucket':>11} {'n':>4} {'precio':>7} {'win real':>9} {'edge':>7} {'retorno/$1':>11}")
    print("-"*54)
    for lo,hi in bins:
        b=[r for r in recs if lo<=r[0]<hi]
        if not b: continue
        mp=sum(r[0] for r in b)/len(b); wr=sum(r[1] for r in b)/len(b)
        ret=sum((1/r[0]-1) if r[1] else -1 for r in b)/len(b)
        print(f"{lo:.2f}-{hi:.2f} {len(b):>4} {mp:>7.1%} {wr:>9.1%} {wr-mp:>+7.1%} {ret:>+10.1%}")
    for thr in (0.85,0.90,0.95):
        b=[r for r in recs if r[0]>=thr]
        if len(b)<8: continue
        wr=sum(r[1] for r in b)/len(b); mp=sum(r[0] for r in b)/len(b)
        ret=sum((1/r[0]-1) if r[1] else -1 for r in b)/len(b)
        se=math.sqrt(wr*(1-wr)/len(b))
        print(f"\nFAVORITOS >= {thr:.2f}:  n={len(b)}  win {wr:.1%} (IC95% {max(0,wr-1.96*se):.1%}-{min(1,wr+1.96*se):.1%})"
              f"  precio {mp:.1%}  retorno/$1 {ret:+.1%}")
    print("\n⚠ CAVEAT (artefacto verificado jul 2026): los favoritos que 'abren' >0.90 en este")
    print("  muestreo son casi todos mercados YA DECIDIDOS con 1-3 trades de liquidación a ~1.00")
    print("  (p.ej. 'ETH above X' que ya se cumplió). Su win=100% y +return son TRIVIALES, no carry.")
    print("  El carry real (mercado a 0.95 durante días sobre evento incierto) NO aparece aquí y")
    print("  exige precio a tiempo fijo ANTES de resolver + filtrar por duración/volumen reales.")

def main():
    maxm=int(sys.argv[1]) if len(sys.argv)>1 else 300
    recs=collect(maxm)
    if len(recs)<20: print("pocas observaciones."); return
    report(recs)

if __name__=="__main__":
    try: sys.stdout.reconfigure(encoding="utf-8")
    except Exception: pass
    main()
