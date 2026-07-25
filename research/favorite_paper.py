"""
EXPERIMENTO #13 — FAVORITE PAPER BOT (DRY): la estrategia del wallet #1 (el top ganador real).

De la ingeniería inversa FORENSE (wallet_forensics + favorite_backtest): el top-z ganador COMPRA EL
FAVORITO fuerte (82-95¢), lo AGUANTA a resolución, y gana. El backtest sobre miles de ventanas: favorito
82-95¢ gana 89.6% vs ask 88.8% = +0.84pp, y GENERALIZA (train +0.83 / test +0.84). Mecanismo: sesgo
FAVORITO-LONGSHOT (favoritos infravalorados porque la gente sobrepaga longshots). Primer edge del proyecto
+EV Y que generaliza. Este bot lo confirma FORWARD en vivo + acumula hacia significancia.

Regla (pre-registrada, del backtest — NADA optimizado):
  · solo 5m · a ENTRY=240s
  · favorito = el lado con MAYOR ask (el que el mercado ve ganador); ask = su ask
  · si ask ∈ [0.82, 0.95] → COMPRAR el favorito al ask (taker, fill garantizado), AGUANTAR a resolución
  · breakeven = ask (sin descuento). El edge debe venir SOLO del sesgo favorito-longshot.

CRITERIO pre-fijado: ≥400 fills resueltos (fino + precio alto = alta varianza → n grande), win>ask con IC
significativo. SIN optional-stopping. Muerte: win≤ask a n≥400.

    python favorite_paper.py             # 24/7 (systemd favorite-paper.service)
    python favorite_paper.py --analyze   # veredicto
Autónomo (stdlib). Log gitignored.
"""
import urllib.request, json, time, csv, os, sys, math

ENTRY   = 240
FAV_MIN = 0.82         # zona del wallet #1 (favorito fuerte); < 82 salió −EV/ruido en el backtest
FAV_MAX = 0.95         # > 95¢ salió sobrevalorado (−0.5pp): el margen no cubre el precio casi-1
LOG = os.path.join(os.path.dirname(__file__), "favorite_paper_log.csv")
HEADER = ["ws", "slug", "fav", "ask", "ask2", "status", "winner", "won", "cid"]

def get(url, tries=2):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "favorite-paper/1.0"})
            with urllib.request.urlopen(req, timeout=15) as r: return json.load(r)
        except Exception:
            if i == tries - 1: return None
            time.sleep(0.5)

def now(): return int(time.time())

def log(row):
    new = not os.path.exists(LOG)
    with open(LOG, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new: w.writerow(HEADER)
        w.writerow(row)

def discover(ws):
    slug = f"btc-updown-5m-{ws}"
    d = get(f"https://gamma-api.polymarket.com/markets?slug={slug}")
    if not (isinstance(d, list) and d): return None
    m = d[0]
    try:
        outs = json.loads(m.get("outcomes") or "[]"); tids = json.loads(m.get("clobTokenIds") or "[]")
    except Exception: return None
    if len(outs) != 2 or len(tids) != 2: return None
    toks = dict(zip(outs, tids))
    if "Up" not in toks or "Down" not in toks: return None
    return {"ws": ws, "slug": slug, "cid": m.get("conditionId"), "toks": toks}

def best_ask(tok):
    b = get(f"https://clob.polymarket.com/book?token_id={tok}")
    if not isinstance(b, dict): return None
    asks = [float(a["price"]) for a in b.get("asks", [])]
    return min(asks) if asks else None

def winner_clob(cid):
    d = get(f"https://clob.polymarket.com/markets/{cid}")
    if not isinstance(d, dict): return None
    for t in d.get("tokens", []):
        if t.get("winner") is True: return t.get("outcome")
    return None

def backfill_pending(verbose=False):
    """Rellena el ganador SOLO vía CLOB (Chainlink), nunca Binance. Idempotente."""
    if not os.path.exists(LOG): return 0
    rows = list(csv.DictReader(open(LOG, encoding="utf-8")))
    if not rows: return 0
    n = 0
    for r in rows:
        if r.get("winner") or not r.get("cid") or not r.get("fav"): continue
        w = winner_clob(r["cid"])
        if w is None: continue
        r["winner"] = w; r["won"] = "1" if w == r["fav"] else "0"
        n += 1; time.sleep(0.1)
    if n:
        with open(LOG, "w", newline="", encoding="utf-8") as f:
            wr = csv.DictWriter(f, fieldnames=HEADER); wr.writeheader(); wr.writerows(rows)
    if verbose: print(f"backfill: {n} filas resueltas por CLOB")
    return n

def run_window(win):
    ws, slug, cid, toks = win["ws"], win["slug"], win["cid"], win["toks"]
    print(f"\n── {slug} — favorito a {ENTRY}s")
    while now() < ws + ENTRY: time.sleep(2)
    au, ad = best_ask(toks["Up"]), best_ask(toks["Down"])
    if au is None or ad is None: return
    fav = "Up" if au > ad else "Down"                    # favorito = mayor ask
    ask = au if fav == "Up" else ad
    ask2 = ad if fav == "Up" else au                     # ask del underdog (contexto)
    if not (FAV_MIN <= ask <= FAV_MAX):
        log([ws, slug, fav, round(ask, 3), round(ask2, 3), "skip", "", "", cid])
        print(f"   skip: favorito {fav} @ {ask:.2f} fuera de [{FAV_MIN},{FAV_MAX}]"); return
    print(f"   BUY favorito {fav} @ {ask:.3f}  (underdog @ {ask2:.3f})")

    # resolución SOLO por el ganador del CLOB (Chainlink). Sin respaldo Binance.
    while now() < ws + 300 + 5: time.sleep(5)
    win_side = None; t0 = now()
    while now() < t0 + 360 and win_side is None:
        win_side = winner_clob(cid)
        if win_side is None: time.sleep(15)
    won = "" if win_side is None else (1 if win_side == fav else 0)
    log([ws, slug, fav, round(ask, 3), round(ask2, 3), "bought", win_side or "", won, cid])
    print(f"   -> winner {win_side or 'PEND'} | won {won}")

def analyze():
    if not os.path.exists(LOG): print("sin log"); return
    backfill_pending()
    rows = list(csv.DictReader(open(LOG, encoding="utf-8")))
    from collections import Counter
    st = Counter(r["status"] for r in rows)
    B = [r for r in rows if r["status"] == "bought" and r["won"] in ("0", "1")]
    print(f"ventanas: {len(rows)}  ·  {dict(st)}")
    print(f"fills resueltos (favorito comprado): {len(B)}")

    def rep(label, rs):
        n = len(rs)
        if not n: print(f"  {label:<20} sin fills"); return
        wr = sum(int(r["won"]) for r in rs) / n; ap = sum(float(r["ask"]) for r in rs) / n
        se = math.sqrt(wr * (1 - wr) / n); edge = (wr - ap) * 100; rel = edge / (ap * 100) * 100 if ap else 0
        sig = "SIG" if wr - 1.96 * se > ap else ("+" if wr > ap else "")
        print(f"  {label:<20} n={n:>4}  win {wr:.1%} (IC {max(0,wr-1.96*se):.1%}-{min(1,wr+1.96*se):.1%})  "
              f"ask {ap:.1%}  EDGE {edge:+.2f}pp  rel {rel:+.1f}% {sig}")
    if not B:
        print("  (aún sin fills resueltos)"); return
    print("\nEDGE (comprar favorito 82-95¢, aguantar — win vs ask; ref backtest +0.84pp, generaliza):")
    rep("TODO (82-95¢)", B)
    for lo, hi, lab in [(0.82, 0.90, "82-90¢"), (0.90, 0.95, "90-95¢")]:
        rep(lab, [r for r in B if lo <= float(r["ask"]) < hi])

    # SOMBRA: los skips resueltos (72-82 y 95-99) — qué habrían dado, sin operar
    S = [r for r in rows if r["status"] == "skip" and r["won"] in ("0", "1")]
    if S:
        print("\nSOMBRA (favoritos fuera de 82-95¢ que NO operamos — resueltos por CLOB):")
        for lo, hi, lab in [(0.62, 0.72, "62-72¢"), (0.72, 0.82, "72-82¢"), (0.95, 1.01, "95-99¢")]:
            rep(lab, [r for r in S if lo <= float(r["ask"]) < hi])

    print("\nVEREDICTO (pre-fijado: ≥400 fills, win>ask significativo):")
    n = len(B); wr = sum(int(r["won"]) for r in B) / n; ap = sum(float(r["ask"]) for r in B) / n
    se = math.sqrt(wr * (1 - wr) / n)
    if n < 400:
        print(f"  → {n}/400 fills — sin veredicto (margen fino + alta varianza exige n grande)")
    elif wr - 1.96 * se > ap:
        print("  → win>ask SIGNIFICATIVO → el edge favorito-longshot es NUESTRO. Primer edge real y confirmado.")
    elif wr > ap:
        print(f"  → win {wr:.1%} > ask {ap:.1%} pero no significativo — seguir acumulando.")
    else:
        print("  → win≤ask con n≥400: el favorito no bate su precio en vivo. Documentar y cerrar.")

def main():
    if "--analyze" in sys.argv: analyze(); return
    if "--resolve" in sys.argv: print(f"rellenadas {backfill_pending(verbose=True)}"); return
    print("=" * 60 + "\n  FAVORITE PAPER BOT (DRY) — comprar el favorito 82-95¢ y aguantar\n" + "=" * 60)
    n = backfill_pending(verbose=True)
    if n: print(f"backfill inicial: {n}")
    seen = set(); last_bf = now()
    while True:
        try:
            t = now(); ws = t - t % 300
            if ws not in seen and t < ws + ENTRY - 10:
                w = discover(ws)
                if w:
                    seen.add(ws); run_window(w)
                    if len(seen) > 500: seen = set(list(seen)[-100:])
            if now() - last_bf > 600:
                nb = backfill_pending(); last_bf = now()
                if nb: print(f"backfill: {nb}")
            time.sleep(5)
        except KeyboardInterrupt: print("\nparado."); break
        except Exception as ex: print("  err:", ex); time.sleep(10)

if __name__ == "__main__":
    try: sys.stdout.reconfigure(encoding="utf-8")
    except Exception: pass
    main()
