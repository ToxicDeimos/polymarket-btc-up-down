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
import urllib.request, json, time, csv, os, sys, math

ENTRY    = 240          # s dentro de la ventana 5m (300s)
MOVE_MIN = 8            # $ |movimiento| mínimo (por debajo, líder≈coinflip sin señal)
MOVE_MAX = 45           # $ máximo (fuerte>$40 apenas deja margen; corte medido)
ASK_MIN  = 0.52         # zona del líder (motor 60-80¢ + coinflip alto)
ASK_MAX  = 0.82
LOG = os.path.join(os.path.dirname(__file__), "momentum_paper_log.csv")
HEADER = ["ws","slug","move","leader","ask","status","winner","won","cid"]

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

def run_window(win):
    ws,slug,cid=win["ws"],win["slug"],win["cid"]
    print(f"\n── {slug} — entrada a los {ENTRY}s")
    while now() < ws+ENTRY: time.sleep(2)
    o=spot_at(ws); e=spot()
    if o is None or e is None: return
    move=e-o
    if not (MOVE_MIN<=abs(move)<=MOVE_MAX):
        print(f"   skip: move ${move:+.0f} fuera de [{MOVE_MIN},{MOVE_MAX}]")
        log([ws,slug,round(move,1),"","","skip_move","","",cid]); return
    leader="Up" if move>0 else "Down"
    ask=best_ask(win["toks"][leader])
    if ask is None: return
    if not (ASK_MIN<=ask<=ASK_MAX):
        print(f"   skip: ask {ask} fuera de [{ASK_MIN},{ASK_MAX}]")
        log([ws,slug,round(move,1),leader,ask,"skip_price","","",cid]); return
    print(f"   TAKER BUY {leader} @ {ask}  (move ${move:+.0f})")
    # resolución: CLOB con reintentos, luego Binance de respaldo (validado 15/15)
    while now() < ws+300+5: time.sleep(5)
    win_side=None; t0=now()
    while now() < t0+120 and win_side is None:
        win_side=winner_clob(cid)
        if win_side is None: time.sleep(15)
    if win_side is None:
        c=spot_at(ws+300)
        if c is not None and o is not None: win_side="Up" if c>o else "Down"
    won = "" if win_side is None else (1 if win_side==leader else 0)
    print(f"   -> winner {win_side} | won {won}")
    log([ws,slug,round(move,1),leader,ask,"taker",win_side or "",won,cid])

def analyze():
    if not os.path.exists(LOG): print("sin log aún"); return
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
    rep("TODO",T)
    print("  — por |move|:")
    for lo,hi,lab in [(0,15,"suave 8-15"),(15,40,"media 15-40"),(40,99,"fuerte 40-45")]:
        rep(lab,[r for r in T if lo<=abs(float(r["move"]))<hi])
    print("  — por zona de ask:")
    for lo,hi,lab in [(0.52,0.62,"52-62c"),(0.62,0.72,"62-72c"),(0.72,0.83,"72-82c")]:
        rep(lab,[r for r in T if lo<=float(r["ask"])<hi])
    n=len(T); wr=sum(int(r["won"]) for r in T)/n; ap=sum(float(r["ask"]) for r in T)/n
    print("\nVEREDICTO (criterio pre-fijado: ≥40 resueltos, EV>0):")
    if n<40: print(f"  → aún {n}/40 trades — sin veredicto")
    elif wr>ap:
        se=math.sqrt(wr*(1-wr)/n)
        if wr-1.96*se>ap: print("  → LA SEÑAL PREDICE (win>ask, significativo). El edge de momentum es NUESTRO también.")
        else: print("  → positivo pero no significativo — seguir (exigir IC a ~80)")
    else: print("  → ≤break-even: 12ª muerte — el edge no se transfiere. Documentar y cerrar.")

def main():
    if "--analyze" in sys.argv: analyze(); return
    print("="*60+"\n  MOMENTUM PAPER BOT (DRY) — comprar el líder tarde (5m)\n"+"="*60)
    seen=set()
    while True:
        try:
            t=now(); ws=t-t%300
            if ws not in seen and t < ws+ENTRY-10:
                w=discover(ws)
                if w:
                    seen.add(ws); run_window(w)
                    if len(seen)>500: seen=set(list(seen)[-100:])
            time.sleep(5)
        except KeyboardInterrupt:
            print("\nparado."); break
        except Exception as ex:
            print("  err:",ex); time.sleep(10)

if __name__=="__main__":
    try: sys.stdout.reconfigure(encoding="utf-8")
    except Exception: pass
    main()
