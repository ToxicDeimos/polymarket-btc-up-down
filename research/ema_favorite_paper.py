"""
ema_favorite_paper.py — EXP #17 (DRY): favorito 52-82¢ FILTRADO por tendencia EMA21 (1m de Binance).

Hallazgo 2026-08-03: el separador que GENERALIZA (train/test, único en todo el proyecto) es precio vs EMA21
en 1m, y el edge es MAYOR cuanto más barato/incierto el favorito (la confirmación de tendencia aporta más):
   ALINEADO (precio del lado del favorito):  52-62¢ +23.9pp · 62-72¢ +17.6pp · 72-82¢ +16.2pp  NETO de fee
   CONTRA (rebote contra-tendencia):         ~−27pp NETO en todas las bandas
La EMA es, en esencia, el MOMENTUM hecho bien: comprar el líder solo cuando la tendencia 1m lo confirma
(evitando los rebotes que revierten). Por eso el momentum crudo moría y esto no.

Este bot lo confirma EN VIVO (paper): a 240s mira el favorito (mayor ask) en 62-82¢, calcula precio vs EMA21
de Binance 1m, y COMPRA solo si está ALINEADO. Los CONTRA se registran en SOMBRA (deben perder → validan la
regla). Métrica: NETO de comisión taker Polymarket crypto = 0.07·p·(1−p). Solo necesita klines 1m (sin lab).

    python3 ema_favorite_paper.py            # 24/7 (systemd)
    python3 ema_favorite_paper.py --analyze  # veredicto: alineado vs contra, neto de fee
    python3 ema_favorite_paper.py --resolve  # rellenar ganadores pendientes por CLOB
Autónomo (stdlib). Log gitignored.
"""
import urllib.request, json, time, csv, os, sys, math

ENTRY  = 240
LO, HI = 0.52, 0.82        # zona incierta. El edge EMA es MAYOR cuanto más barato (52-62¢ +23.9pp neto vs
                           # 72-82¢ +16.2pp): más incierto = la confirmación de tendencia aporta más.
EMA_N  = 21                # EMA sobre closes 1m
MIN_VERDICT = 80           # fills comprados para veredicto (el efecto es ENORME → confirma rápido)
LOG = os.path.join(os.path.dirname(__file__), "ema_favorite_log.csv")
HEADER = ["ws", "slug", "fav", "ask", "ask2", "aligned", "px", "ema21", "status", "winner", "won", "cid"]

def FEE(p): return 0.07 * p * (1 - p)

def get(url, tries=2):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "ema-fav/1.0"})
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

def ema_state():
    """(px, EMA21) de BTC 1m Binance sobre velas CERRADAS (descarta la vela en formación, como el
    backtest que validó la regla). px = último close cerrado; EMA21 sobre esos closes. o (None, None)."""
    d = get("https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval=1m&limit=80")
    if not isinstance(d, list) or len(d) < EMA_N + 2: return None, None
    closes = [float(c[4]) for c in d[:-1]]      # [:-1] = descarta la última vela (aún abierta)
    k = 2 / (EMA_N + 1); e = closes[0]
    for p in closes[1:]: e = p * k + e * (1 - k)
    return closes[-1], e

def backfill_pending(verbose=False):
    """Rellena ganador por CLOB (Chainlink) para los fills in-zone (bought/against). Idempotente."""
    if not os.path.exists(LOG): return 0
    rows = list(csv.DictReader(open(LOG, encoding="utf-8")))
    n = 0
    for r in rows:
        if r.get("winner") or not r.get("cid") or not r.get("fav"): continue
        if r.get("status") not in ("bought", "against"): continue
        w = winner_clob(r["cid"])
        if w is None: continue
        r["winner"] = w; r["won"] = "1" if w == r["fav"] else "0"; n += 1; time.sleep(0.1)
    if n:
        with open(LOG, "w", newline="", encoding="utf-8") as f:
            wr = csv.DictWriter(f, fieldnames=HEADER); wr.writeheader(); wr.writerows(rows)
    if verbose: print(f"backfill: {n} filas resueltas por CLOB")
    return n

def run_window(win):
    ws, slug, cid, toks = win["ws"], win["slug"], win["cid"], win["toks"]
    print(f"\n── {slug} @ {ENTRY}s")
    while now() < ws + ENTRY: time.sleep(2)
    au, ad = best_ask(toks["Up"]), best_ask(toks["Down"])
    if au is None or ad is None: return
    fav = "Up" if au > ad else "Down"
    ask = au if fav == "Up" else ad; ask2 = ad if fav == "Up" else au
    px, e21 = ema_state()
    if px is None:
        print("   sin EMA (Binance no responde) — skip esta ventana"); return
    aligned = 1 if ((px > e21) == (fav == "Up")) else 0
    if not (LO <= ask <= HI):
        log([ws, slug, fav, round(ask, 3), round(ask2, 3), aligned, round(px, 1), round(e21, 1), "skip", "", "", cid])
        print(f"   skip: favorito {fav} @ {ask:.2f} fuera de [{LO},{HI}]"); return
    status = "bought" if aligned else "against"   # alineado → COMPRA; contra → SOMBRA (debe perder)
    while now() < ws + 300 + 5: time.sleep(5)      # resolver por CLOB (Chainlink)
    wside = None; t0 = now()
    while now() < t0 + 360 and wside is None:
        wside = winner_clob(cid)
        if wside is None: time.sleep(15)
    won = "" if wside is None else (1 if wside == fav else 0)
    log([ws, slug, fav, round(ask, 3), round(ask2, 3), aligned, round(px, 1), round(e21, 1), status, wside or "", won, cid])
    tag = "BUY ✓alineado" if aligned else "sombra ✗contra"
    print(f"   {tag}  {fav} @ {ask:.3f}  px{px:.0f} ema{e21:.0f}  → winner {wside or 'PEND'} | won {won}")

def analyze():
    if not os.path.exists(LOG): print("sin log"); return
    backfill_pending()
    rows = list(csv.DictReader(open(LOG, encoding="utf-8")))
    from collections import Counter
    st = Counter(r["status"] for r in rows)
    print(f"ventanas: {len(rows)}  ·  {dict(st)}")

    def rep(label, rs):
        rs = [r for r in rs if r.get("won") in ("0", "1")]
        n = len(rs)
        if n < 20: print(f"  {label:<24} n={n} (pocos fills resueltos)"); return None
        wr = sum(int(r["won"]) for r in rs) / n; ap = sum(float(r["ask"]) for r in rs) / n
        net = sum(int(r["won"]) - float(r["ask"]) - FEE(float(r["ask"])) for r in rs) / n * 100
        se = math.sqrt(wr * (1 - wr) / n) * 100                   # SE del win ≈ SE del edge (ask ~fijo)
        print(f"  {label:<24} n={n:>4}  win {wr:.1%}  ask {ap:.1%}  crudo {(wr-ap)*100:+.2f}pp  "
              f"NETO {net:+.2f}pp  (SE~{se:.1f}pp)")
        return {"n": n, "wr": wr, "ap": ap, "net": net, "se": se}

    B = [r for r in rows if r["status"] == "bought"]
    A = [r for r in rows if r["status"] == "against"]
    print("\nEDGE en 52-82¢ con filtro EMA21 1m (NETO de comisión taker):")
    b = rep("COMPRADO (✓alineado)", B)
    a = rep("SOMBRA (✗contra)", A)
    print("  desglose del COMPRADO por banda (edge esperado mayor en la barata):")
    for lo, hi, lab in [(0.52, 0.62, "52-62¢"), (0.62, 0.72, "62-72¢"), (0.72, 0.821, "72-82¢")]:
        rep("  " + lab, [r for r in B if r.get("ask") and lo <= float(r["ask"]) < hi])

    print("\nVEREDICTO (pre-fijado: ≥80 comprados; alineado NETO>0 signif. Y contra NETO<0 = la regla vale):")
    if b is None or b["n"] < MIN_VERDICT:
        print(f"  → {b['n'] if b else 0}/{MIN_VERDICT} comprados — sin veredicto aún (efecto grande → confirma rápido).")
    elif b["net"] > 0 and b["net"] - 1.96 * b["se"] > 0:
        extra = f" y CONTRA pierde (NETO {a['net']:+.2f}pp)" if a and a["net"] < 0 else ""
        print(f"  → ✅ REGLA CONFIRMADA: alineado NETO {b['net']:+.2f}pp SIGNIFICATIVO tras comisión (n={b['n']}){extra}.")
        print("     Replicable con solo klines 1m. Candidato REAL a live (margen de sobra sobre la fee).")
    elif b["net"] > 0:
        print(f"  → alineado NETO {b['net']:+.2f}pp positivo pero no significativo (n={b['n']}) — seguir.")
    else:
        print(f"  → alineado NETO {b['net']:+.2f}pp ≤0: la regla NO se sostiene forward. Revisar.")

def main():
    if "--analyze" in sys.argv: analyze(); return
    if "--resolve" in sys.argv: print(f"rellenadas {backfill_pending(verbose=True)}"); return
    print("=" * 62 + f"\n  EMA-FAVORITE PAPER (DRY) — favorito 62-82¢ filtrado por EMA21 1m\n" + "=" * 62)
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
