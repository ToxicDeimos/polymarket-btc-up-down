"""
EXPERIMENTO #3 — MOMENTUM PAPER BOT (DRY): replica la mitad GANADORA de los ganadores.

Del lab (2509 fills resueltos, 3 días, ROI +18.2% ponderado por dinero):
  los ganadores COMPRAN EL LÍDER como TAKERS, tarde en la ventana, SOLO en 5m.
  suave/media positivo (pasa el test de régimen), fase 240-600s la mejor, 60-80¢ el motor.

Regla (pre-registrada de lo medido, NADA optimizado):
  · SOLO mercado 5m
  · a los ENTRY=240s (falta 1 min): move = spot(t) − spot(ws)
  · si |move| ∈ [8, 45] $  (sweet: media $15-40; suave también positivo)
  · líder = Up si move>0, si no Down
  · si ask(líder) ∈ [0.52, 0.82]  → COMPRAR AL ASK (taker simulado, fill garantizado por definición)
  · aguantar a resolución. SIN cancel (ellos aguantan). Breakeven = ask (sin descuento maker):
    el edge debe venir SOLO de la señal.

CRITERIO DE MUERTE pre-fijado: tras ≥40 trades resueltos, continuar solo si EV>0 (win>ask medio);
a ~80 exigir IC. Si ≤break-even → 12ª muerte y se documenta.

    python momentum_paper.py             # correr 24/7 (systemd momentum-paper.service)
    python momentum_paper.py --analyze   # veredicto del log
Autónomo (stdlib). Log: momentum_paper_log.csv (gitignored).
"""
import urllib.request, json, time, csv, os, sys, math, bisect

ENTRY    = 240          # s dentro de la ventana 5m (300s)
MOVE_MIN = 8            # $ |movimiento| mínimo (por debajo, líder≈coinflip sin señal)
MOVE_MAX = 45           # $ máximo (fuerte>$40 apenas deja margen; corte medido)
ASK_MIN  = 0.52         # BRAZO A v2 — zona validada EN NUESTRA FASE (200-280s) con sus fills:
ASK_MAX  = 0.72         #   52-62¢ EV +44.4¢ (n=6) · 62-72¢ +16.4¢ (n=11) · 72-82¢ −26.3¢ (n=33!)
                        # v2 = corrección de DERIVACIÓN: el cruce original no condicionaba por fase,
                        # y a los 240s la banda 72-82¢ es negativa en sus propios datos. Log v1 archivado.
ASKB_MAX = 0.40         # BRAZO B (pre-registrado tras verificar 37 fills con ganador REAL 78.4%
                        # a precio 33.5%): líder DESPRECIADO — divergencia mercado/spot. Zona
                        # 0.40-0.52 sigue excluida (validada negativa, EV −11.8¢).
CL_DIV_MIN = 3          # $ — DIVERGENCIA REAL de Chainlink: se movió ≥$3 EN CONTRA del move de
                        # Binance a 240s. Por debajo = Chainlink casi plano/lag, NO divergencia.
                        # El lab ve la señal AQUÍ (discrepa → coinflip 53.8%), no en el "confirma"
                        # que es el ~90% por defecto y ≈ tasa base (no filtra nada).
LOG = os.path.join(os.path.dirname(__file__), "momentum_paper_log.csv")
HEADER = ["ws","slug","move","leader","ask","status","winner","won","cid","res","ask2","cl_confirm","accel","cl_div"]
OLD_HEADERS = [["ws","slug","move","leader","ask","status","winner","won","cid"],
               ["ws","slug","move","leader","ask","status","winner","won","cid","res"],
               ["ws","slug","move","leader","ask","status","winner","won","cid","res","ask2"],
               ["ws","slug","move","leader","ask","status","winner","won","cid","res","ask2","cl_confirm"],
               ["ws","slug","move","leader","ask","status","winner","won","cid","res","ask2","cl_confirm","accel"]]

def ensure_log():
    """Migra el log a HEADER actual (añade columnas nuevas vacías, conserva todo)."""
    if not os.path.exists(LOG): return
    with open(LOG,encoding="utf-8") as f: first=f.readline().strip()
    if first==",".join(HEADER): return
    if first.split(",") in OLD_HEADERS:
        rows=list(csv.DictReader(open(LOG,encoding="utf-8")))
        with open(LOG,"w",newline="",encoding="utf-8") as f:
            w=csv.DictWriter(f,fieldnames=HEADER); w.writeheader()
            for r in rows: w.writerow({k:r.get(k,"") for k in HEADER})
        print(f"log migrado (+res), {len(rows)} filas conservadas")

def get(url, tries=2):
    for i in range(tries):
        try:
            req=urllib.request.Request(url, headers={"User-Agent":"momentum-paper/1.0"})
            with urllib.request.urlopen(req, timeout=15) as r: return json.load(r)
        except Exception:
            if i==tries-1: return None
            time.sleep(0.5)

def now(): return int(time.time())
def spot():
    d=get("https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT")
    return float(d["price"]) if d else None
def spot_at(ts):
    k=get(f"https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval=1s&startTime={ts*1000}&endTime={(ts+2)*1000}&limit=1")
    return float(k[0][4]) if k else None

def discover(ws):
    """Ventana 5m determinista por slug vía Gamma (lección del lab: el feed va con minutos de lag)."""
    slug=f"btc-updown-5m-{ws}"
    d=get(f"https://gamma-api.polymarket.com/markets?slug={slug}")
    if not (isinstance(d,list) and d): return None
    m=d[0]
    try:
        outs=json.loads(m.get("outcomes") or "[]"); tids=json.loads(m.get("clobTokenIds") or "[]")
    except Exception: return None
    if len(outs)!=2 or len(tids)!=2: return None
    toks=dict(zip(outs,tids))
    if "Up" not in toks or "Down" not in toks: return None
    return {"ws":ws,"slug":slug,"cid":m.get("conditionId"),"toks":toks}

def best_ask(tok):
    b=get(f"https://clob.polymarket.com/book?token_id={tok}")
    if not isinstance(b,dict): return None
    asks=[float(a["price"]) for a in b.get("asks",[])]
    return min(asks) if asks else None

def winner_clob(cid):
    d=get(f"https://clob.polymarket.com/markets/{cid}")
    if not isinstance(d,dict): return None
    for t in d.get("tokens",[]):
        if t.get("winner") is True: return t.get("outcome")
    return None

def log(row):
    new=not os.path.exists(LOG)
    with open(LOG,"a",newline="",encoding="utf-8") as f:
        w=csv.writer(f)
        if new: w.writerow(HEADER)
        w.writerow(row)

CL_DIR = os.path.join(os.path.dirname(__file__), "lab")
def chainlink_move(ws, t):
    """Move de CHAINLINK (ws→t) leído del CSV del colector (lab/chainlink_*.csv) — la fuente
    con la que Polymarket LIQUIDA. Devuelve None si no hay datos (colector parado o sin snapshot)."""
    rows=[]
    for day in {time.strftime("%Y%m%d",time.gmtime(ws)), time.strftime("%Y%m%d",time.gmtime(t))}:
        p=os.path.join(CL_DIR,f"chainlink_{day}.csv")
        if not os.path.exists(p): continue
        try:
            with open(p,encoding="utf-8") as f:
                for ln in f:
                    a=ln.split(",")
                    if a and a[0]=="ts": continue
                    try: rows.append((int(a[0]),float(a[1])))
                    except Exception: pass
        except Exception: pass
    if not rows: return None
    rows.sort(); ks=[r[0] for r in rows]
    def at(ts):
        i=bisect.bisect_right(ks,ts)-1
        return rows[i][1] if i>=0 and ts-rows[i][0]<=90 else None
    a=at(ws); b=at(t)
    return (b-a) if (a is not None and b is not None) else None

def backfill_cldiv():
    """Rellena cl_div (divergencia Chainlink a 240s) para filas que no lo tienen, leyendo los CSV
    PERSISTENTES del colector (lab/chainlink_*.csv). OFFLINE (sin red) e idempotente: así el
    histórico entero se puede evaluar YA, sin esperar a que entren trades nuevos. Devuelve nº filas."""
    if not os.path.exists(LOG): return 0
    rows=list(csv.DictReader(open(LOG,encoding="utf-8")))
    if not rows or "cl_div" not in rows[0]: return 0     # log sin migrar aún
    n=0
    for r in rows:
        if r.get("cl_div") not in (None,""): continue
        if r.get("status") not in ("taker","taker_b","skip_price"): continue
        try: ws=int(r["ws"]); mv=float(r["move"])
        except Exception: continue
        clm=chainlink_move(ws, ws+ENTRY)                 # move CL de ventana→entrada (240s)
        if clm is None: continue
        r["cl_div"]="yes" if (abs(clm)>=CL_DIV_MIN and (clm>0)!=(mv>0)) else "no"
        n+=1
    if n:
        with open(LOG,"w",newline="",encoding="utf-8") as f:
            w=csv.DictWriter(f,fieldnames=list(rows[0].keys())); w.writeheader(); w.writerows(rows)
    return n

def backfill_accel():
    """Rellena accel (¿el move sigue VIVO a 240s? = velocidad últimos 30s en la dirección del líder)
    para filas sin él, desde lab/spot_*.csv (Binance) del colector. Feature PRE-OBSERVABLE (no
    look-ahead: usa spot en ws+210 y ws+240, antes de resolver). OFFLINE, idempotente. Devuelve nº.
    Así el candidato Nº1 (A-v3) se evalúa sobre TODO el histórico ya, no solo desde que se logueó."""
    if not os.path.exists(LOG): return 0
    rows=list(csv.DictReader(open(LOG,encoding="utf-8")))
    if not rows or "accel" not in rows[0]: return 0
    cache={}
    def series(day):
        if day in cache: return cache[day]
        arr=[]; p=os.path.join(CL_DIR,f"spot_{day}.csv")
        if os.path.exists(p):
            try:
                with open(p,encoding="utf-8") as f:
                    for ln in f:
                        a=ln.split(",")
                        if a and a[0]=="ts": continue
                        try: arr.append((int(a[0]),float(a[1])))
                        except Exception: pass
            except Exception: pass
        arr.sort(); cache[day]=arr; return arr
    def at(ts,maxage=8):
        arr=series(time.strftime("%Y%m%d",time.gmtime(ts)))
        if not arr: return None
        ks=[x[0] for x in arr]; i=bisect.bisect_right(ks,ts)-1
        return arr[i][1] if i>=0 and ts-arr[i][0]<=maxage else None
    n=0
    for r in rows:
        if r.get("accel") not in (None,""): continue
        if r.get("status") not in ("taker","taker_b","skip_price"): continue
        try: ws=int(r["ws"]); mv=float(r["move"])
        except Exception: continue
        e=at(ws+ENTRY); e30=at(ws+ENTRY-30)                  # spot a 240s y a 210s (últimos 30s)
        if e is None or e30 is None: continue
        r["accel"]="yes" if ((e-e30)>0)==(mv>0) else "no"
        n+=1
    if n:
        with open(LOG,"w",newline="",encoding="utf-8") as f:
            w=csv.DictWriter(f,fieldnames=list(rows[0].keys())); w.writeheader(); w.writerows(rows)
    return n

def backfill_pending(verbose=False):
    """Rellena pendientes y CORRIGE filas resueltas por el antiguo respaldo Binance, usando
    SIEMPRE el ganador real del CLOB (la liquidación de Polymarket). Idempotente: solo toca
    filas con res != 'clob'. Corre al arrancar y cada ~10 min en el loop."""
    if not os.path.exists(LOG): return (0,0)
    rows=list(csv.DictReader(open(LOG,encoding="utf-8")))
    if not rows: return (0,0)
    filled=fixed=touched=0
    for r in rows:
        if r.get("status") not in ("taker","taker_b","skip_price"): continue
        if r.get("res")=="clob": continue
        w=winner_clob(r.get("cid"))
        if w is None: continue
        if r.get("status")=="skip_price":
            # SOMBRA: resolver también los skips con líder logueado — mide qué habría pasado
            # en las zonas que NO operamos (72-82¢ v1, >82¢) sin arriesgar el experimento.
            r["winner"]=w; r["res"]="clob"; touched+=1; time.sleep(0.1); continue
        won="1" if w==r.get("leader") else "0"
        if r.get("won") not in ("0","1"): filled+=1
        elif r.get("won")!=won:
            fixed+=1
            if verbose: print(f"   CORREGIDA {r.get('slug')}: won {r.get('won')} -> {won} (CLOB={w})")
        r["winner"]=w; r["won"]=won; r["res"]="clob"; touched+=1
        time.sleep(0.1)
    if touched:
        with open(LOG,"w",newline="",encoding="utf-8") as f:
            wcsv=csv.DictWriter(f,fieldnames=list(rows[0].keys())); wcsv.writeheader(); wcsv.writerows(rows)
    return (filled,fixed)

def run_window(win):
    ws,slug,cid=win["ws"],win["slug"],win["cid"]
    print(f"\n── {slug} — entrada a los {ENTRY}s")
    while now() < ws+ENTRY: time.sleep(2)
    o=spot_at(ws); e=spot()
    if o is None or e is None: return
    move=e-o
    if not (MOVE_MIN<=abs(move)<=MOVE_MAX):
        print(f"   skip: move ${move:+.0f} fuera de [{MOVE_MIN},{MOVE_MAX}]")
        log([ws,slug,round(move,1),"","","skip_move","","",cid,"","","","",""]); return
    leader="Up" if move>0 else "Down"
    ask=best_ask(win["toks"][leader])
    ask2=best_ask(win["toks"]["Down" if leader=="Up" else "Up"])   # lado FADE (sombra contraria)
    # SOMBRA Chainlink: ¿confirma el líder-por-Binance? (confirma = caso por defecto ~90%, no filtra)
    clm=chainlink_move(ws, now())
    cl_confirm = "" if clm is None else ("yes" if (clm>0)==(move>0) else "no")
    # SOMBRA DIVERGENCIA (lo que SÍ tiene filo según el lab): Chainlink se movió ≥$3 EN CONTRA
    cl_div = "" if clm is None else ("yes" if (abs(clm)>=CL_DIV_MIN and (clm>0)!=(move>0)) else "no")
    # SOMBRA ACELERACIÓN (el hallazgo sólido del lab): ¿el move sigue vivo a 240s? (últimos 30s)
    e30=spot_at(now()-30)
    accel = "" if e30 is None else ("yes" if ((e-e30)>0)==(move>0) else "no")
    if ask is None: return
    if ASK_MIN<=ask<=ASK_MAX:      status="taker"    # brazo A: momentum confirmado
    elif 0.05<=ask<=ASKB_MAX:      status="taker_b"  # brazo B: líder despreciado (divergencia)
    else:
        print(f"   skip: ask {ask} fuera de zonas A/B")
        log([ws,slug,round(move,1),leader,ask,"skip_price","","",cid,"",ask2 or "",cl_confirm,accel,cl_div]); return
    print(f"   {'TAKER' if status=='taker' else 'TAKER-B'} BUY {leader} @ {ask}  (move ${move:+.0f})")
    # resolución SOLO por el ganador REAL del CLOB (la liquidación de Polymarket, que sigue a
    # Chainlink). El respaldo Binance se ELIMINÓ: medido 92% de acierto = 8% de error, incluso
    # en moves de $12-15 (dentro de nuestra señal). Lo no resuelto queda pendiente y lo rellena
    # backfill_pending() en el propio loop.
    while now() < ws+300+5: time.sleep(5)
    win_side=None; res=""; t0=now()
    while now() < t0+360 and win_side is None:
        win_side=winner_clob(cid)
        if win_side is None: time.sleep(15)
    if win_side is not None: res="clob"
    won = "" if win_side is None else (1 if win_side==leader else 0)
    print(f"   -> winner {win_side or 'PENDIENTE'} | won {won}")
    log([ws,slug,round(move,1),leader,ask,status,win_side or "",won,cid,res,ask2 or "",cl_confirm,accel,cl_div])

def analyze():
    if not os.path.exists(LOG): print("sin log aún"); return
    ensure_log()
    nd=backfill_cldiv(); na=backfill_accel()
    if nd or na: print(f"backfill desde CSV del colector: cl_div {nd} filas, accel {na} filas")
    rows=list(csv.DictReader(open(LOG,encoding="utf-8")))
    from collections import Counter
    st=Counter(r["status"] for r in rows)
    print(f"ventanas: {len(rows)}  estados: {dict(st)}")
    T=[r for r in rows if r["status"]=="taker" and r["won"] in ("0","1")]
    print(f"trades resueltos: {len(T)}")
    if not T: return
    def rep(label,rs):
        n=len(rs)
        if not n: print(f"  {label:>14}  sin trades"); return
        wr=sum(int(r["won"]) for r in rs)/n
        ap=sum(float(r["ask"]) for r in rs)/n
        ev=sum((1/float(r["ask"])-1) if r["won"]=="1" else -1 for r in rs)/n
        se=math.sqrt(wr*(1-wr)/n)
        print(f"  {label:>14}  n={n:>3}  win {wr:.1%} (IC {max(0,wr-1.96*se):.1%}-{min(1,wr+1.96*se):.1%})"
              f"  ask medio {ap:.1%}  EV/trade {ev:+.1%}")
    rsrc=Counter((r.get("res") or "pre-columna") for r in rows if r["status"] in ("taker","taker_b") and r["won"] in ("0","1"))
    print(f"fuente de resolución de los resueltos: {dict(rsrc)}")
    rep("TODO (A)",T)
    print("  — por |move|:")
    for lo,hi,lab in [(0,15,"suave 8-15"),(15,40,"media 15-40"),(40,99,"fuerte 40-45")]:
        rep(lab,[r for r in T if lo<=abs(float(r["move"]))<hi])
    print("  — por zona de ask:")
    for lo,hi,lab in [(0.52,0.62,"52-62c"),(0.62,0.72,"62-72c"),(0.72,0.83,"72-82c")]:
        rep(lab,[r for r in T if lo<=float(r["ask"])<hi])
    import datetime as _dt
    def _d(r): return _dt.datetime.utcfromtimestamp(int(r["ws"])).strftime("%m-%d")
    print("  — por DÍA (¿aguanta el régimen, o vive de un día bueno?):")
    for d in sorted({_d(r) for r in T}):
        rep(d, [r for r in T if _d(r)==d])
    n=len(T); wr=sum(int(r["won"]) for r in T)/n; ap=sum(float(r["ask"]) for r in T)/n
    print("\nVEREDICTO BRAZO A (pre-fijado: ≥40 resueltos, EV>0):")
    if n<40: print(f"  → aún {n}/40 trades — sin veredicto")
    elif wr>ap:
        se=math.sqrt(wr*(1-wr)/n)
        if wr-1.96*se>ap: print("  → LA SEÑAL PREDICE (win>ask, significativo). El edge de momentum es NUESTRO también.")
        else: print("  → positivo pero no significativo — seguir (exigir IC a ~80)")
    else: print("  → ≤break-even: 12ª muerte — el edge no se transfiere. Documentar y cerrar.")

    S=[r for r in rows if r["status"]=="skip_price" and r.get("winner") and r.get("leader") and r.get("ask")]
    if S:
        print("\nSOMBRA (skips resueltos — qué habría pasado comprando al líder, SIN operar):")
        for lo,hi,lab in [(0.40,0.52,"40-52c (conflicto)"),(0.72,0.82,"72-82c (v1 excl.)"),
                          (0.82,0.95,"82-95c"),(0.95,1.01,">95c")]:
            rep(lab,[dict(r,won=("1" if r["winner"]==r["leader"] else "0"))
                     for r in S if lo<=float(r["ask"])<hi])

    F=[r for r in rows if r.get("winner") and r.get("leader") and r.get("ask2")]
    if F:
        print("\nSOMBRA-FADE (comprar el lado CONTRA el move — mide sus longshots/coinflip fade, SIN operar):")
        for lo,hi,lab in [(0.0,0.20,"fade <20c (longshot)"),(0.20,0.40,"fade 20-40c"),(0.40,0.55,"fade 40-55c")]:
            rep(lab,[dict(r,ask=r["ask2"],won=("1" if r["winner"]!=r["leader"] else "0"))
                     for r in F if lo<=float(r["ask2"])<hi])

    Ac=[r for r in T if r.get("accel") in ("yes","no")]
    print(f"\nFILTRO ACELERACIÓN sobre A (el hallazgo SÓLIDO del lab — ¿el move sigue vivo a 240s?):")
    if Ac:
        rep("A acelera", [r for r in Ac if r["accel"]=="yes"])
        rep("A frena",   [r for r in Ac if r["accel"]=="no"])
        print("  (si 'acelera' gana más que 'frena' → el filtro que esquiva los días malos)")
        # TEST DE MECANISMO: ¿la mitad 'acelera' sobrevivió los días malos (07-22 = A crudo −28%)?
        print("  — ACELERA vs FRENA por DÍA (¿el filtro esquiva los días de reversión?):")
        def _seg(rs):
            if not rs: return "     —          "
            wr=sum(int(r["won"]) for r in rs)/len(rs)
            ev=sum((1/float(r["ask"])-1) if r["won"]=="1" else -1 for r in rs)/len(rs)
            return f"n={len(rs):>2} win {wr:>5.1%} EV {ev*100:>+6.1f}%"
        for d in sorted({_d(r) for r in Ac}):
            acy=[r for r in Ac if _d(r)==d and r["accel"]=="yes"]
            acn=[r for r in Ac if _d(r)==d and r["accel"]=="no"]
            evy=(sum((1/float(r["ask"])-1) if r["won"]=="1" else -1 for r in acy)/len(acy)) if acy else 0
            evn=(sum((1/float(r["ask"])-1) if r["won"]=="1" else -1 for r in acn)/len(acn)) if acn else 0
            mark=" ✓ filtro salva" if (acy and acn and evy>0 and evn<0) else ""
            print(f"    {d}   acelera {_seg(acy)}   |   frena {_seg(acn)}{mark}")
    else:
        print("  (aún sin datos de aceleración — se loguea desde ahora)")

    C=[r for r in T if r.get("cl_div") in ("yes","no")]
    print(f"\nFILTRO DIVERGENCIA CHAINLINK sobre A (el lab ve la señal en la DIVERGENCIA, no en el ~90% que confirma):")
    if C:
        rep("A alineado", [r for r in C if r["cl_div"]=="no"])    # MANTIENE — CL no diverge (normal)
        rep("A diverge",  [r for r in C if r["cl_div"]=="yes"])   # QUITARÍA — CL ≥$3 en contra
        nd=sum(1 for r in C if r["cl_div"]=="yes")
        print(f"  (diverge = Chainlink ≥${CL_DIV_MIN} EN CONTRA del move de Binance a 240s; {nd}/{len(C)} son divergencias."
              f" Si 'diverge' pierde → el filtro es esquivar esas ventanas)")
    else:
        print("  (aún sin datos de divergencia — el colector debe tener chainlink_*.csv de esas ventanas)")

    B=[r for r in rows if r["status"]=="taker_b" and r["won"] in ("0","1")]
    print(f"\nBRAZO B — líder despreciado <40¢ (pre-registrado: ≥25 resueltos, EV>0; referencia lab 78.4% a 33.5%):")
    rep("TODO (B)",B)
    Bac=[r for r in B if r.get("accel") in ("yes","no")]
    if Bac:
        print("  filtro ACELERACIÓN sobre B (el move sigue vivo → el despreciado es mispricing real?):")
        rep("B acelera", [r for r in Bac if r["accel"]=="yes"])
        rep("B frena",   [r for r in Bac if r["accel"]=="no"])
    Bc=[r for r in B if r.get("cl_div") in ("yes","no")]
    if Bc:
        print("  filtro DIVERGENCIA Chainlink sobre B:")
        rep("B alineado", [r for r in Bc if r["cl_div"]=="no"])
        rep("B diverge",  [r for r in Bc if r["cl_div"]=="yes"])
    Bp=[r for r in rows if r["status"]=="taker_b" and r["won"] not in ("0","1")]
    if Bp: print(f"  ({len(Bp)} sin resolver — brazo B NO usa respaldo Binance, esperar al CLOB)")
    if len(B)>=25:
        wb=sum(int(r["won"]) for r in B)/len(B); ab=sum(float(r["ask"]) for r in B)/len(B)
        print("  →", "el brazo B TAMBIÉN transfiere" if wb>ab else "el brazo B no transfiere (era post-hoc)")

def main():
    if "--analyze" in sys.argv: analyze(); return
    if "--resolve" in sys.argv:
        ensure_log(); f,x=backfill_pending(verbose=True); nd=backfill_cldiv(); na=backfill_accel()
        print(f"rellenadas {f} | corregidas {x} | cl_div {nd} | accel {na}"); return
    print("="*60+"\n  MOMENTUM PAPER BOT (DRY) — comprar el líder tarde (5m)\n"+"="*60)
    ensure_log()
    f,x=backfill_pending(verbose=True)
    nd=backfill_cldiv(); na=backfill_accel()
    if f or x or nd or na: print(f"backfill inicial: rellenadas {f}, corregidas {x}, cl_div {nd}, accel {na}")
    seen=set(); last_bf=now()
    while True:
        try:
            t=now(); ws=t-t%300
            if ws not in seen and t < ws+ENTRY-10:
                w=discover(ws)
                if w:
                    seen.add(ws); run_window(w)
                    if len(seen)>500: seen=set(list(seen)[-100:])
            if now()-last_bf>600:
                f,x=backfill_pending(); nd=backfill_cldiv(); na=backfill_accel(); last_bf=now()
                if f or x or nd or na: print(f"backfill: rellenadas {f}, corregidas {x}, cl_div {nd}, accel {na}")
            time.sleep(5)
        except KeyboardInterrupt:
            print("\nparado."); break
        except Exception as ex:
            print("  err:",ex); time.sleep(10)

if __name__=="__main__":
    try: sys.stdout.reconfigure(encoding="utf-8")
    except Exception: pass
    main()
