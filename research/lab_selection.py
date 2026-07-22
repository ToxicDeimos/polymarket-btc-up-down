"""
LAB experimento #4 — MINERÍA DE SELECCIÓN. La pregunta central del laboratorio:
¿hay una feature PRE-OBSERVABLE (computable a 240s, sin mirar el resultado) que separe los
momentum GANADORES de los PERDEDORES de los ganadores?

Si la hay:  es (a) su SELECCIÓN — lo que usan para elegir — y (b) nuestro FILTRO de régimen
            para no operar los días/momentos malos. Las dos preguntas son la misma.
Si no la hay en los datos observables:  su edge necesita info privada/velocidad que no tenemos,
            y la respuesta es gestión de riesgo (sizing), no filtro. Conclusión honesta igual de válida.

Sobre los fills momentum de los ganadores (compraron el líder, 5m, fase 200-280, move 8-45), computa
features pre-observables desde spot/chainlink del lab y mide si predicen el resultado, con
SPLIT TRAIN/TEST POR DÍA (si solo funciona en los días de train = overfit, se descarta).
Features:
  · ACELERACIÓN: ¿el move de los últimos 30s va en la dirección del líder (sigue vivo) o ya gira?
  · CHAINLINK: ¿Chainlink confirma la dirección de Binance? (necesita datos; se acumulan)
Resolución por Binance (rápida; caveat 92% vs Chainlink — ok para momentum, líder claro).

    python lab_selection.py
"""
import csv, os, sys, glob, bisect, time, urllib.request, json, datetime as dt

DIR=os.path.join(os.path.dirname(__file__),"lab")

def get(url, tries=2):
    for i in range(tries):
        try:
            req=urllib.request.Request(url, headers={"User-Agent":"labsel/1"})
            with urllib.request.urlopen(req, timeout=15) as r: return json.load(r)
        except Exception:
            if i==tries-1: return None
            time.sleep(0.3)

_sc={}
def spot_at(ts):
    if ts in _sc: return _sc[ts]
    k=get(f"https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval=1s&startTime={ts*1000}&endTime={(ts+2)*1000}&limit=1")
    _sc[ts]=float(k[0][4]) if k else None
    return _sc[ts]

def load(name, days):
    rows=[]
    for d in days:
        p=os.path.join(DIR,f"{name}_{d}.csv")
        if os.path.exists(p): rows+=list(csv.DictReader(open(p,encoding="utf-8")))
    return rows

def wr(rs): return (sum(x["won"] for x in rs)/len(rs)) if rs else None

def show(rs, cond, label):
    a=[x for x in rs if cond(x)]; b=[x for x in rs if not cond(x)]
    wa,wb=wr(a),wr(b)
    print(f"  {label}")
    print(f"     SÍ:  n={len(a):>4}  win {wa:.1%}" if wa is not None else f"     SÍ:  n=0")
    print(f"     NO:  n={len(b):>4}  win {wb:.1%}" if wb is not None else f"     NO:  n=0")
    return wa,wb

def main():
    days=sorted({os.path.basename(p).split('_')[1][:8] for p in glob.glob(os.path.join(DIR,'fills_*.csv'))})
    if not days: print("sin datos"); return
    fills=load("fills",days); spot=load("spot",days); cl=load("chainlink",days)
    print(f"días: {', '.join(days)}  | fills {len(fills)} | spot {len(spot)} | chainlink {len(cl)}")

    sidx=sorted((int(s["ts"]),float(s["price"])) for s in spot if s.get("price")); sts=[x[0] for x in sidx]
    def lspot(ts,maxage=8):
        i=bisect.bisect_right(sts,ts)-1
        return sidx[i][1] if i>=0 and ts-sidx[i][0]<=maxage else None
    clidx=sorted((int(c["ts"]),float(c["price"])) for c in cl if c.get("price")); clts=[x[0] for x in clidx]
    def lcl(ts,maxage=45):
        i=bisect.bisect_right(clts,ts)-1
        return clidx[i][1] if i>=0 and ts-clidx[i][0]<=maxage else None

    seen=set(); R=[]
    for f in fills:
        if f.get("trade_side")!="BUY": continue
        slug=f.get("slug","") or ""
        if "-5m-" not in slug: continue
        key=(f.get("tx"),f.get("ts_trade"),f.get("price"),f.get("outcome"))
        if key in seen: continue
        seen.add(key)
        try: t=int(f["ts_trade"]); ws=int(slug.split("-")[-1])
        except Exception: continue
        if not (200<=t-ws<280): continue                    # nuestra fase 240s
        o=lspot(ws,12); e=lspot(t,12)                        # spot del PROPIO lab (no Binance)
        if o is None or e is None: continue
        move=e-o
        if not (8<=abs(move)<=45): continue                 # nuestra banda de move
        leader="Up" if move>0 else "Down"
        if f.get("outcome")!=leader: continue               # SOLO momentum (compraron el líder)
        s0=lspot(t); s30=lspot(t-30)
        if s0 is None or s30 is None: continue
        vel=s0-s30                                           # move de los últimos 30s
        accel=(vel>0)==(move>0)                              # ¿sigue en la dirección del líder?
        cl0=lcl(t); clw=lcl(ws)
        clmove=(cl0-clw) if (cl0 is not None and clw is not None) else None
        cl_agree=None if clmove is None else ((clmove>0)==(move>0))
        c=lspot(ws+300,12)                                   # cierre desde el spot del lab
        if c is None: continue
        won=1 if leader==("Up" if c>o else "Down") else 0
        R.append({"day":dt.datetime.utcfromtimestamp(ws).strftime("%m-%d"),
                  "won":won,"accel":accel,"cl_agree":cl_agree})

    if len(R)<30:
        print(f"\npocos fills momentum en fase con datos de libro ({len(R)}) — deja correr el colector más.")
        return
    print(f"\nfills momentum (5m, fase 200-280, move 8-45) con feature: {len(R)}  |  win base {wr(R):.1%}")

    print("\n=== FEATURE 1: ¿el move sigue ACELERANDO a 240s? (móv últimos 30s en la dirección del líder) ===")
    wa,wb=show(R, lambda x:x["accel"], "acelerando (SÍ) vs frenando/girando (NO):")
    daylist=sorted({x["day"] for x in R})
    if len(daylist)>=4 and wa is not None and wb is not None:
        h=len(daylist)//2; trd=set(daylist[:h]); ted=set(daylist[h:])
        tr=[x for x in R if x["day"] in trd]; te=[x for x in R if x["day"] in ted]
        gtr=(wr([x for x in tr if x['accel']]) or 0)-(wr([x for x in tr if not x['accel']]) or 0)
        gte=(wr([x for x in te if x['accel']]) or 0)-(wr([x for x in te if not x['accel']]) or 0)
        print(f"\n  TRAIN ({','.join(sorted(trd))}): gap acel−fren = {gtr:+.1%}")
        print(f"  TEST  ({','.join(sorted(ted))}): gap acel−fren = {gte:+.1%}")
        if gtr>0.05 and gte>0.05: print("  → la feature GENERALIZA (acel gana más en train Y test) = selección/filtro REAL ✓")
        elif gtr>0.05: print("  → funciona en train pero NO en test = overfit, descartar")
        else: print("  → sin señal clara en la aceleración")

    Rc=[x for x in R if x["cl_agree"] is not None]
    print(f"\n=== FEATURE 2: ¿CHAINLINK confirma la dirección de Binance? (n con dato={len(Rc)}) ===")
    if len(Rc)>=20:
        show(Rc, lambda x:x["cl_agree"], "Chainlink de acuerdo (SÍ) vs discrepa (NO):")
        print("  (si 'de acuerdo' gana mucho más → ELLOS miden en Chainlink = selección copiable)")
    else:
        print(f"  solo {len(Rc)} fills con Chainlink — el colector lo captura desde hace poco; re-correr en 1-2 días.")

if __name__=="__main__":
    try: sys.stdout.reconfigure(encoding="utf-8")
    except Exception: pass
    main()
