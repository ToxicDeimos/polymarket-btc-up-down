"""
EXPERIMENTO #9 — MAKER2 PAPER (DRY): cobrar el spread en opciones BARATAS, con cola CONSERVADORA.

maker_edge.py demostró un edge MAKER estructural (sobrevive leave-out, 65% de wallets con mediana
+2.8-3.2pp, estable a 5s y 12s de frescura de libro). El dinero está en <40¢ (retorno relativo
<20c +36%, 20-40c +11%). maker_paper (11ª muerte) NO probó esto: posteaba ask−2¢ (DENTRO del spread,
máxima selección adversa), solo 38-48¢ (excluía lo bueno) y condicionado a fadear un spike.

Este bot corrige los tres y ataca de frente el riesgo que puede tumbarlo — la PRIORIDAD DE COLA:

  1. Cada ventana 5m, a ENTRY=195s, mira el libro. Lado BARATO = el de menor ask.
  2. Si el mejor bid del lado barato está en [0.03, 0.40] → POSTEA en el mejor bid (cobra el spread
     entero, no ask−2¢). Registra la profundidad que había DELANTE (bid_sz) = nuestra posición en cola.
  3. FILL CONSERVADOR: NO nos damos por llenos porque alguien imprima a nuestro precio. Acumulamos
     el volumen VENDIDO a <= nuestro bid tras postear; solo contamos FILL cuando ese volumen supera
     lo que había DELANTE en la cola (bid_sz) — es decir, cuando de verdad nos habría tocado.
     Esto modela selección adversa REAL: te llenan cuando llega flujo vendedor (precio cayendo).
  4. Aguanta a resolución (CLOB, Chainlink). won = el lado barato ganó.

Compara EV de lo que REALMENTE se llenó (con selección adversa dentro) vs el edge poblacional.
Si el EV llenado sigue > 0 tras la cola conservadora → primer edge desplegable del proyecto.

CRITERIO DE MUERTE pre-fijado: >=40 fills; si EV<=0 → documentar. Optional-stopping prohibido.

    python maker2_paper.py            # loop 24/7 (systemd)
    python maker2_paper.py --analyze  # veredicto
Autónomo (stdlib). Log gitignored.
"""
import urllib.request, json, time, csv, os, sys, math, bisect

ENTRY      = 195       # s dentro de la ventana 5m (mediana de entrada de los makers ganadores)
BID_MIN    = 0.03      # no postear por debajo (ruido/resolución)
BID_MAX    = 0.40      # zona BARATA: donde el retorno relativo del spread es grande
POLL       = 3         # s entre sondeos del libro/cinta/spot mientras la orden está viva
MIN_VERDICT = 40       # v2 PROFUNDO: el edge es mucho mayor (+15.75pp deep vs +1.76 al toque) → confirma
                       # con menos fills (n > 3.84·p(1−p)/edge² ≈ 30 para +15.75pp). 40 basta.
POST_OFFSET = 0.02     # v2: postear POR DEBAJO del mejor bid, no al toque. maker_adverse (12.937 fills):
                       # edge maker crece +1.76pp (al toque) → +7.67pp (2-5¢) → +15.75pp (>5¢ profundo).
                       # Mecanismo: un bid profundo caza dips TRANSITORIOS que revierten. Trade-off:
                       # menor tasa de llenado (te llenan menos) = el único incógnito, se mide operándolo.
CANCEL_BPS = 3.0       # gestión de SELECCIÓN ADVERSA: retirar el bid si BTC (spot Binance) se mueve
                       # >=3bps EN CONTRA del lado comprado desde que posteamos. Umbral ÓPTIMO medido
                       # sobre 12.305 fills maker reales (maker_adverse.py): el cubo ">2bps en contra"
                       # es el único −EV (−0.64pp); cancelar a 3bps sube el edge de +4.31 a +8.20pp
                       # reteniendo el 68% de los fills. Es el ingrediente del maker profesional.
LOG = os.path.join(os.path.dirname(__file__), "maker2_paper_log.csv")
# BRACKET de llenado × CANCELACIÓN. fill_opt = frente de cola (primer print). status filled = final de
# cola (vol vendido > cola). cancelled/vol_precancel = con gestión adversa (fills ANTES de cancelar).
HEADER = ["ws","slug","cheap","best_bid","best_ask","bid","queue_ahead","vol_hit","fill_opt",
          "status","fill_price","winner","won","cid","cancelled","vol_precancel"]
OLD_HEADERS = [
    ["ws","slug","cheap","best_bid","best_ask","bid","queue_ahead","vol_hit",
     "status","fill_price","winner","won","cid"],                                   # v0 (13)
    ["ws","slug","cheap","best_bid","best_ask","bid","queue_ahead","vol_hit","fill_opt",
     "status","fill_price","winner","won","cid"],                                   # v1 (14, +fill_opt)
]

def get(url, tries=2):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "maker2/1.0"})
            with urllib.request.urlopen(req, timeout=15) as r: return json.load(r)
        except Exception:
            if i == tries - 1: return None
            time.sleep(0.5)

def now(): return int(time.time())

def ensure_log():
    """Migra el log a HEADER actual (cadena v0/v1 → nuevo). Deriva columnas nuevas de lo ya logueado:
    fill_opt del vol_hit (si hubo venta a nuestro precio, el frente de cola se habría llenado);
    vol_precancel = vol_hit (filas viejas no monitoreaban cancel → equivalen a 'sin cancelar')."""
    if not os.path.exists(LOG): return
    with open(LOG, encoding="utf-8") as f: first = f.readline().strip()
    if first == ",".join(HEADER): return
    if first.split(",") in OLD_HEADERS:
        rows = list(csv.DictReader(open(LOG, encoding="utf-8")))
        for r in rows:
            st = r.get("status")
            try: v = float(r.get("vol_hit") or 0)
            except Exception: v = 0.0
            if "fill_opt" not in r or r.get("fill_opt") in (None, ""):
                r["fill_opt"] = "yes" if (st == "filled" or v > 0) else ("no" if st == "no_fill" else "")
            r["cancelled"] = ""                       # desconocido en filas viejas
            r["vol_precancel"] = r.get("vol_hit", "")  # sin cancel ⇒ igual que vol_hit
        with open(LOG, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=HEADER); w.writeheader()
            for r in rows: w.writerow({k: r.get(k, "") for k in HEADER})
        print(f"log migrado a v2 (+cancelled/vol_precancel), {len(rows)} filas conservadas")
    else:
        print("(!) cabecera de log inesperada — revisar antes de continuar")

def binance_spot():
    d = get("https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT")
    try: return float(d["price"]) if d else None
    except Exception: return None

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

def book(tok):
    """(mejor_bid, tam_bid, mejor_ask) del token."""
    b = get(f"https://clob.polymarket.com/book?token_id={tok}")
    if not isinstance(b, dict): return None
    bids = b.get("bids", []); asks = b.get("asks", [])
    bb = max((float(x["price"]) for x in bids), default=None)
    ba = min((float(x["price"]) for x in asks), default=None)
    bsz = sum(float(x["size"]) for x in bids if abs(float(x["price"]) - (bb or -1)) < 1e-9) if bb else 0.0
    return (bb, bsz, ba)

def winner_clob(cid):
    d = get(f"https://clob.polymarket.com/markets/{cid}")
    if not isinstance(d, dict): return None
    for t in d.get("tokens", []):
        if t.get("winner") is True: return t.get("outcome")
    return None

def backfill_pending(verbose=False):
    """Rellena el ganador (SOLO vía CLOB = liquidación real de Polymarket) en filas que quedaron
    pendientes porque el CLOB tardó >5min en publicarlo. Idempotente: solo toca filas sin 'winner'
    que tengan lado (cheap) y cid. Resuelve también no_fill/skip_price = datos-sombra (qué habría
    pasado). NUNCA usa Binance. Corre al arrancar y cada ~10 min."""
    if not os.path.exists(LOG): return 0
    rows = list(csv.DictReader(open(LOG, encoding="utf-8")))
    if not rows: return 0
    n = 0
    for r in rows:
        if r.get("winner"): continue                       # ya resuelto
        if not r.get("cheap") or not r.get("cid"): continue
        w = winner_clob(r["cid"])
        if w is None: continue                             # aún no liquidado → sigue pendiente
        r["winner"] = w
        r["won"] = "1" if w == r["cheap"] else "0"
        n += 1
        time.sleep(0.1)
    if n:
        with open(LOG, "w", newline="", encoding="utf-8") as f:
            wr = csv.DictWriter(f, fieldnames=HEADER); wr.writeheader(); wr.writerows(rows)
    if verbose: print(f"backfill: {n} filas resueltas por CLOB")
    return n

def run_window(win):
    ws, slug, cid, toks = win["ws"], win["slug"], win["cid"], win["toks"]
    print(f"\n── {slug} — entrada maker a {ENTRY}s")
    while now() < ws + ENTRY: time.sleep(2)
    bu, bd = book(toks["Up"]), book(toks["Down"])
    if not bu or not bd or bu[2] is None or bd[2] is None: return
    cheap = "Up" if bu[2] < bd[2] else "Down"          # lado con menor ask = barato
    bb, bsz, ba = (bu if cheap == "Up" else bd)
    if bb is None: return
    bid = round(bb - POST_OFFSET, 3)                    # v2: POR DEBAJO del mejor bid (caza dips que revierten)
    if not (BID_MIN <= bid <= BID_MAX):
        log([ws, slug, cheap, bb, ba, bid, "", "", "", "skip_price", "", "", "", cid]);
        print(f"   skip: bid {bid} (best {bb}−{POST_OFFSET}) fuera de [{BID_MIN},{BID_MAX}]"); return
    queue = round(bsz, 1)                               # bids POR ENCIMA (mejor bid) = lo que se vende antes de llegar a nosotros
    sp0 = binance_spot()                                # spot de referencia para la cancelación
    print(f"   POST bid {bid} en {cheap}  (best_bid {bb}, ask {ba}, cola arriba {queue}, spot {sp0})")

    # ── bracket de cola (suelo/techo) × GESTIÓN ADVERSA (cancelar si BTC se gira) ────────────────
    #   fill_opt (techo)=frente de cola; status filled (suelo)=final de cola. NO se rompe el bucle.
    #   cancelled=yes cuando BTC va >=CANCEL_BPS en contra de 'cheap': se congela vol_precancel = los
    #   fills que YA teníamos antes de cancelar (los posteriores no cuentan en la versión con-cancel).
    seen = set(); vol_hit = 0.0; fill_opt = "no"; status = "no_fill"; last = ws + ENTRY
    cancelled = "no"; vol_precancel = 0.0
    while now() < ws + 300:
        feed = get(f"https://data-api.polymarket.com/trades?market={cid}&limit=100") or []
        for t in feed:
            if t.get("outcome") != cheap or t.get("side") != "SELL": continue
            h = (t.get("transactionHash"), t.get("timestamp"), t.get("price"), t.get("size"))
            if h in seen: continue
            try:
                tp = float(t.get("price") or 1); tt = int(t.get("timestamp") or 0); tsz = float(t.get("size") or 0)
            except Exception: continue
            if tt < last or tp > bid: continue          # solo flujo NUEVO vendido a <= nuestro bid
            seen.add(h); vol_hit += tsz
        if vol_hit > 0 and fill_opt == "no":
            fill_opt = "yes"; print(f"   fill OPTIMISTA @ {bid} (primer print vendido)")
        if vol_hit > queue and status != "filled":
            status = "filled"; print(f"   fill CONSERVADOR @ {bid} (vol {vol_hit:.1f} > cola {queue})")
        # CANCELACIÓN: ¿BTC se movió >=CANCEL_BPS en contra del lado comprado desde que posteamos?
        if cancelled == "no" and sp0:
            sp = binance_spot()
            if sp:
                adv = (sp - sp0) / sp0 * 10000
                against = (-adv if cheap == "Up" else adv)    # >0 = BTC en contra de 'cheap'
                if against >= CANCEL_BPS:
                    cancelled = "yes"; vol_precancel = vol_hit
                    print(f"   CANCEL bid ({cheap}: BTC {against:.1f}bps en contra) — fills previos {vol_hit:.1f}")
        time.sleep(POLL)
    if cancelled == "no": vol_precancel = vol_hit          # nunca cancelado ⇒ con-cancel = sin-cancel

    # ── resolución por CLOB (Chainlink) ──
    while now() < ws + 300 + 5: time.sleep(3)
    win_side = None; t0 = now()
    while now() < t0 + 300 and win_side is None:
        win_side = winner_clob(cid)
        if win_side is None: time.sleep(15)
    won = "" if win_side is None else (1 if win_side == cheap else 0)
    log([ws, slug, cheap, bb, ba, bid, queue, round(vol_hit, 1), fill_opt,
         status, bid if status == "filled" else "", win_side or "", won, cid,
         cancelled, round(vol_precancel, 1)])
    print(f"   -> cons={status} opt={fill_opt} cancel={cancelled} | winner {win_side or 'PEND'} | won {won}")

def _ev(rs):
    if not rs: return None
    n = len(rs); wr = sum(int(r["won"]) for r in rs) / n; ap = sum(float(r["bid"]) for r in rs) / n
    return wr - ap

def analyze():
    if not os.path.exists(LOG): print("sin log"); return
    ensure_log(); backfill_pending()
    rows = list(csv.DictReader(open(LOG, encoding="utf-8")))
    from collections import Counter
    def _vpc(r):
        try: return float(r.get("vol_precancel") or 0)
        except Exception: return 0.0
    def _deep(r):   # v2 = posteado por debajo del mejor bid (best_bid − bid ≥ 1¢). Separa del viejo al-toque.
        try: return (float(r.get("best_bid") or 0) - float(r.get("bid") or 0)) >= 0.01
        except Exception: return False
    posted = [r for r in rows if r.get("status") in ("filled", "no_fill") and _deep(r)]
    Fc  = [r for r in rows if _deep(r) and r.get("status") == "filled" and r.get("won") in ("0", "1")]  # SUELO
    Fo  = [r for r in rows if _deep(r) and r.get("fill_opt") == "yes" and r.get("won") in ("0", "1")]    # TECHO
    Foc = [r for r in rows if _deep(r) and _vpc(r) > 0 and r.get("won") in ("0", "1")]                   # TECHO+CANCEL
    n_touch = len([r for r in rows if not _deep(r) and r.get("fill_opt") == "yes" and r.get("won") in ("0", "1")])
    print(f"ventanas: {len(rows)}  ·  {dict(Counter(r['status'] for r in rows))}")
    print(f"(v2 PROFUNDO: análisis sobre fills posteados <best_bid. Fills al-toque viejos (referencia): {n_touch})")
    def _fr(cond): return len([r for r in rows if _deep(r) and cond(r)]) / len(posted) if posted else 0
    frc = _fr(lambda r: r.get('status') == 'filled')
    fro = _fr(lambda r: r.get('fill_opt') == 'yes')
    froc = _fr(lambda r: _vpc(r) > 0)
    ncancel = len([r for r in rows if _deep(r) and r.get("cancelled") == "yes"])
    print(f"posteadas: {len(posted)}  ·  canceladas: {ncancel}  ·  tasa llenado — "
          f"CONSERVADOR {frc:.0%} / OPTIMISTA {fro:.0%} / OPTIMISTA+CANCEL {froc:.0%}")

    def rep(label, rs):
        n = len(rs)
        if not n: print(f"  {label:<24} sin fills"); return
        wr = sum(int(r["won"]) for r in rs) / n; ap = sum(float(r["bid"]) for r in rs) / n
        ev = wr - ap; se = math.sqrt(wr * (1 - wr) / n); rel = ev / ap * 100 if ap else 0
        sig = "SIG" if wr - 1.96 * se > ap else ("+" if wr > ap else "")
        print(f"  {label:<24} n={n:>4}  win {wr:.1%} (IC {max(0,wr-1.96*se):.1%}-{min(1,wr+1.96*se):.1%})  "
              f"bid {ap:.1%}  EDGE {ev*100:+.2f}pp  rel {rel:+.1f}% {sig}")

    print("\nBRACKET × GESTIÓN ADVERSA (el bot objetivo = TECHO+CANCEL: frente de cola + cancelar a 3bps):")
    rep("SUELO (final cola)",     Fc)
    rep("TECHO (frente cola)",    Fo)
    rep("TECHO+CANCEL (objetivo)", Foc)
    if Fo and Foc:
        print(f"  → efecto de cancelar sobre el techo: EDGE {(_ev(Fo) or 0)*100:+.2f} → {(_ev(Foc) or 0)*100:+.2f}pp "
              f"(referencia lab maker_adverse: +4.31 → +8.20pp)")
    print("  — TECHO+CANCEL por zona de bid:")
    for lo, hi, lab in [(0, .20, "<20c"), (.20, .40, "20-40c")]:
        rep(f"  {lab}", [r for r in Foc if lo <= float(r["bid"]) < hi])

    # ── DIAGNÓSTICO DE SELECCIÓN ADVERSA (el que responde "¿esto funciona?") ────────────────────
    # ¿ganamos MENOS en las ventanas que LLENAMOS que en las que NO? Si un maker lento se lleva los
    # perdedores (longshot cayendo = alguien vende = nos llena) y se pierde los ganadores (longshot
    # sube tranquilo, nadie vende, no nos llena), el win de las llenadas << el de las no-llenadas.
    # Usa TODAS las ventanas resueltas (toque+profundo) = máxima potencia, no espera al EV.
    RES = [r for r in rows if r.get("status") in ("filled", "no_fill") and r.get("won") in ("0", "1")]
    fill_w = [r for r in RES if r.get("fill_opt") == "yes"]
    nofl_w = [r for r in RES if r.get("fill_opt") != "yes"]
    def _wr(rs): return (sum(int(r["won"]) for r in rs) / len(rs)) if rs else None
    wf, wn = _wr(fill_w), _wr(nofl_w)
    print("\nDIAGNÓSTICO SELECCIÓN ADVERSA — ¿llenamos los PERDEDORES y nos perdemos los GANADORES?")
    if wf is not None: print(f"  ventanas que LLENAMOS:     n={len(fill_w):>4}  win {wf:.1%}  precio medio {sum(float(r['bid']) for r in fill_w)/len(fill_w):.1%}")
    if wn is not None: print(f"  ventanas que NO llenamos:  n={len(nofl_w):>4}  win {wn:.1%}  (sombra: qué habría pasado)")
    if wf is not None and wn is not None:
        gap = (wf - wn) * 100
        print(f"  → GAP: {gap:+.1f}pp." + (
            "  ⚠ MUY negativo = nos llevamos los perdedores = selección adversa SEVERA (un maker de Pi no puede)."
            if gap < -5 else
            "  ~plano = nuestros fills son representativos; el rojo es bajo volumen + varianza, no sesgo."))

    print(f"\nVEREDICTO (umbral {MIN_VERDICT} sobre TECHO+CANCEL — opciones baratas = alta varianza):")
    et = _ev(Foc)
    if len(Foc) < MIN_VERDICT:
        print(f"  → {len(Foc)}/{MIN_VERDICT} fills(techo+cancel), sin veredicto (n bajo = ruido puro)")
    elif et <= 0:
        print("  → ni con cancelación es +EV → el spread no compensa la selección adversa desde una Pi. Muerte.")
    else:
        n = len(Foc); wr = sum(int(r["won"]) for r in Foc) / n; ap = sum(float(r["bid"]) for r in Foc) / n
        se = math.sqrt(wr * (1 - wr) / n)
        print("  → EDGE MAKER +EV con cancelación" +
              (" (SIGNIFICATIVO) → primer edge desplegable del proyecto." if wr - 1.96 * se > ap
               else " (positivo, no significativo aún) → seguir."))

def main():
    if "--analyze" in sys.argv: analyze(); return
    if "--resolve" in sys.argv: ensure_log(); print(f"rellenadas {backfill_pending(verbose=True)}"); return
    print("=" * 60 + "\n  MAKER2 PAPER (DRY) — cobrar el spread en <40¢, cola conservadora\n" + "=" * 60)
    ensure_log()
    n = backfill_pending(verbose=True)
    if n: print(f"backfill inicial: {n} filas")
    seen = set(); last_bf = now()
    while True:
        try:
            t = now(); ws = t - t % 300
            if ws not in seen and t < ws + ENTRY - 5:
                w = discover(ws)
                if w:
                    seen.add(ws); run_window(w)
                    if len(seen) > 500: seen = set(list(seen)[-100:])
            if now() - last_bf > 600:
                nb = backfill_pending(); last_bf = now()
                if nb: print(f"backfill: {nb} filas")
            time.sleep(3)
        except KeyboardInterrupt: print("\nparado."); break
        except Exception as ex: print("  err:", ex); time.sleep(10)

if __name__ == "__main__":
    try: sys.stdout.reconfigure(encoding="utf-8")
    except Exception: pass
    main()
