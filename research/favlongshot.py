"""
Mide el sesgo FAVORITO-LONGSHOT en sports resueltos de Polymarket (edge estadístico, sin velocidad).

Para cada match-winner resuelto con volumen: precio de APERTURA (media de los primeros K trades)
de cada lado + si ese lado ganó. Bin por precio → curva de calibración precio→win real.
  - longshots (precio bajo) que ganan MENOS que su precio = sobrepreciados
  - favoritos (precio alto) que ganan MÁS que su precio = infravalorados
  → apostar al favorito / contra el longshot y aguantar a resolución = +EV, sin feed ni velocidad.

    python favlongshot.py [max_markets]
Autónomo (stdlib). CSV de obs en favlongshot_obs.csv (gitignored).
"""
import urllib.request, json, time, ast, csv, os, sys

K = 8            # primeros trades = "apertura"
VOLMIN = int(sys.argv[2]) if len(sys.argv)>2 else 800
SPORTS = ("wta","atp","itf","dota2","cs2","lol","npb","kbo","mlb","nba","fifwc","ucl","bra","soccer","tennis")
BAD = ("handicap","over","under","total","rounds","kills","map ","first ","spread","-1.5","-6.5","-3.5","+")
OBS = os.path.join(os.path.dirname(__file__),"favlongshot_obs.csv")

def get(url, tries=2):
    for i in range(tries):
        try:
            req=urllib.request.Request(url, headers={"User-Agent":"flb/1"})
            with urllib.request.urlopen(req, timeout=25) as r: return json.load(r)
        except Exception:
            if i==tries-1: return None
            time.sleep(0.4)

def is_mw(m, outs):
    q=(m.get("question","") or "").lower()
    if "vs" not in q and " v " not in q: return False
    if any(b in q for b in BAD): return False
    if set(o.lower() for o in outs) & {"over","under","yes","no"}: return False
    return len(outs)==2

def collect(maxm):
    markets=[]; off=0
    while len(markets)<maxm and off<5000:
        d=get(f"https://gamma-api.polymarket.com/markets?closed=true&limit=100&order=id&ascending=false&offset={off}")
        if not isinstance(d,list) or not d: break
        for m in d:
            slug=m.get("slug","") or ""
            if not any(slug.startswith(s) for s in SPORTS): continue
            try: outs=ast.literal_eval(m["outcomes"]); pr=[float(x) for x in ast.literal_eval(m["outcomePrices"])]
            except Exception: continue
            if sorted(pr)!=[0.0,1.0]: continue
            if not is_mw(m,outs): continue
            if float(m.get("volumeNum") or 0)<VOLMIN: continue
            win=outs[0] if pr[0]>0.5 else outs[1]
            markets.append((m,outs,win))
        off+=100; time.sleep(0.05)
    print(f"match-winner sports resueltos (vol>{VOLMIN}): {len(markets)}  — extrayendo apertura...")

    obs=[]  # (open_price, won, league)
    used=0
    for m,outs,win in markets:
        cid=m.get("conditionId")
        tr=get(f"https://data-api.polymarket.com/trades?market={cid}&limit=500")
        if not isinstance(tr,list) or not tr or len(tr)>=500: continue   # sin trades o truncado (open no fiable)
        tr2=sorted(tr, key=lambda t:int(t.get("timestamp") or 0))[:K]
        acc=[]
        for t in tr2:
            try: p=float(t.get("price"))
            except Exception: continue
            acc.append(p if t.get("outcome")==outs[0] else 1-p)   # prob implícita de outs[0]
        if len(acc)<3: continue
        op0=sum(acc)/len(acc)
        if not (0.02<op0<0.98): continue
        lg=(m.get("slug","") or "").split("-")[0]
        obs.append((round(op0,4), 1 if outs[0]==win else 0, lg))
        obs.append((round(1-op0,4), 1 if outs[1]==win else 0, lg))
        used+=1
        time.sleep(0.12)
    print(f"mercados usados: {used}  →  {len(obs)} observaciones\n")
    with open(OBS,"w",newline="",encoding="utf-8") as f:
        w=csv.writer(f); w.writerow(["open_price","won","league"]); w.writerows(obs)
    return obs

def report(obs):
    bins=[(i/10,(i+1)/10) for i in range(10)]
    print(f"{'bucket':>10} {'n':>5} {'precio medio':>12} {'win real':>9} {'sesgo':>8}")
    print("-"*52)
    tot_edge=0; tot_n=0
    for lo,hi in bins:
        b=[o for o in obs if lo<=o[0]<hi]
        if not b: continue
        mp=sum(o[0] for o in b)/len(b); wr=sum(o[1] for o in b)/len(b)
        diff=wr-mp
        flag = "FAV+" if diff>0.03 else ("LONG-" if diff<-0.03 else "")
        print(f"{lo:.1f}-{hi:.1f}   {len(b):>5} {mp:>12.1%} {wr:>9.1%} {diff:>+7.1%} {flag}")
    # test direccional: correlación signo(precio-0.5) con (win-precio)
    lows=[o for o in obs if o[0]<0.35]; highs=[o for o in obs if o[0]>0.65]
    if lows and highs:
        le=sum(o[1]-o[0] for o in lows)/len(lows); he=sum(o[1]-o[0] for o in highs)/len(highs)
        print(f"\nLONGSHOTS (<0.35): n={len(lows)}  win−precio medio = {le:+.1%}  (negativo = sobrepreciados)")
        print(f"FAVORITOS (>0.65): n={len(highs)}  win−precio medio = {he:+.1%}  (positivo = infravalorados)")
        print("\nVEREDICTO:")
        if le<-0.03 and he>0.03:
            print("  → SESGO FAVORITO-LONGSHOT confirmado: apostar favoritos / contra longshots = +EV")
        elif le<-0.05 or he>0.05:
            print("  → sesgo parcial (un extremo) — prometedor, más datos")
        else:
            print("  → sin sesgo claro: los precios están bien calibrados (eficiente)")

def main():
    maxm=int(sys.argv[1]) if len(sys.argv)>1 else 200
    obs=collect(maxm)
    if len(obs)<20: print("pocas observaciones."); return
    report(obs)

if __name__=="__main__":
    try: sys.stdout.reconfigure(encoding="utf-8")
    except Exception: pass
    main()
