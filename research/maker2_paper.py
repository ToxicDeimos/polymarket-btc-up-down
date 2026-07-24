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

ENTRY     = 195        # s dentro de la ventana 5m (mediana de entrada de los makers ganadores)
BID_MIN   = 0.03       # no postear por debajo (ruido/resolución)
BID_MAX   = 0.40       # zona BARATA: donde el retorno relativo del spread es grande
POLL      = 3          # s entre sondeos del libro/cinta mientras la orden está viva
LOG = os.path.join(os.path.dirname(__file__), "maker2_paper_log.csv")
HEADER = ["ws","slug","cheap","best_bid","best_ask","bid","queue_ahead","vol_hit",
          "status","fill_price","winner","won","cid"]

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
    if not os.path.exists(LOG): return
    with open(LOG, encoding="utf-8") as f: first = f.readline().strip()
    if first != ",".join(HEADER):
        print(f"(!) cabecera de log inesperada — revisar antes de continuar")

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

def run_window(win):
    ws, slug, cid, toks = win["ws"], win["slug"], win["cid"], win["toks"]
    print(f"\n── {slug} — entrada maker a {ENTRY}s")
    while now() < ws + ENTRY: time.sleep(2)
    bu, bd = book(toks["Up"]), book(toks["Down"])
    if not bu or not bd or bu[2] is None or bd[2] is None: return
    cheap = "Up" if bu[2] < bd[2] else "Down"          # lado con menor ask = barato
    bb, bsz, ba = (bu if cheap == "Up" else bd)
    if bb is None: return
    bid = round(bb, 3)                                  # posteamos EN el mejor bid (cobra el spread)
    if not (BID_MIN <= bid <= BID_MAX):
        log([ws, slug, cheap, bb, ba, bid, "", "", "skip_price", "", "", "", cid]);
        print(f"   skip: bid {bid} fuera de [{BID_MIN},{BID_MAX}]"); return
    queue = round(bsz, 1)                               # profundidad DELANTE de nosotros en la cola
    tok = toks[cheap]
    print(f"   POST bid {bid} en {cheap}  (ask {ba}, cola delante {queue})")

    # ── cola CONSERVADORA: solo FILL cuando el volumen vendido a <= bid supera la cola de delante ──
    seen = set(); vol_hit = 0.0; status = "no_fill"; last = ws + ENTRY
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
        if vol_hit > queue:                             # ya nos habría tocado en la cola
            status = "filled"; print(f"   FILL @ {bid}  (vol vendido {vol_hit:.1f} > cola {queue})"); break
        time.sleep(POLL)

    # ── resolución por CLOB (Chainlink) ──
    while now() < ws + 300 + 5: time.sleep(3)
    win_side = None; t0 = now()
    while now() < t0 + 300 and win_side is None:
        win_side = winner_clob(cid)
        if win_side is None: time.sleep(15)
    won = "" if win_side is None else (1 if win_side == cheap else 0)
    log([ws, slug, cheap, bb, ba, bid, queue, round(vol_hit, 1),
         status, bid if status == "filled" else "", win_side or "", won, cid])
    print(f"   -> {status} | winner {win_side or 'PEND'} | won {won}")

def analyze():
    if not os.path.exists(LOG): print("sin log"); return
    rows = list(csv.DictReader(open(LOG, encoding="utf-8")))
    F = [r for r in rows if r["status"] == "filled" and r["won"] in ("0", "1")]
    from collections import Counter
    print(f"ventanas: {len(rows)}  ·  {dict(Counter(r['status'] for r in rows))}")
    print(f"FILLS resueltos: {len(F)}")
    if not F: print("  (aún sin fills — el bot solo cuenta fill cuando la cola de delante se agota)"); return

    def rep(label, rs):
        n = len(rs)
        if not n: print(f"  {label:<18} sin fills"); return
        wr = sum(int(r["won"]) for r in rs) / n
        ap = sum(float(r["bid"]) for r in rs) / n
        ev = wr - ap
        se = math.sqrt(wr * (1 - wr) / n)
        rel = ev / ap * 100 if ap else 0
        sig = "SIG" if wr - 1.96 * se > ap else ("+" if wr > ap else "")
        print(f"  {label:<18} n={n:>4}  win {wr:.1%} (IC {max(0,wr-1.96*se):.1%}-{min(1,wr+1.96*se):.1%})  "
              f"bid {ap:.1%}  EDGE {ev*100:+.2f}pp  rel {rel:+.1f}% {sig}")
    rep("TODO", F)
    print("  — por zona de bid:")
    for lo, hi, lab in [(0, .20, "<20c"), (.20, .40, "20-40c")]:
        rep(lab, [r for r in F if lo <= float(r["bid"]) < hi])
    # tasa de llenado y sesgo de selección adversa
    posted = [r for r in rows if r["status"] in ("filled", "no_fill")]
    fillrate = len([r for r in rows if r["status"] == "filled"]) / len(posted) if posted else 0
    print(f"\n  tasa de llenado: {fillrate:.0%}  (posteadas {len(posted)})")
    print("  VEREDICTO (pre-fijado ≥40 fills, EV>0):")
    n = len(F); wr = sum(int(r["won"]) for r in F) / n; ap = sum(float(r["bid"]) for r in F) / n
    if n < 40: print(f"    → {n}/40 fills, sin veredicto")
    elif wr > ap:
        se = math.sqrt(wr * (1 - wr) / n)
        print("    → EDGE MAKER se sostiene tras selección adversa" +
              (" (SIGNIFICATIVO)" if wr - 1.96 * se > ap else " (positivo, no significativo aún)"))
    else:
        print("    → ≤break-even: la selección adversa se come el spread. 13ª muerte, documentar.")

def main():
    if "--analyze" in sys.argv: analyze(); return
    print("=" * 60 + "\n  MAKER2 PAPER (DRY) — cobrar el spread en <40¢, cola conservadora\n" + "=" * 60)
    ensure_log()
    seen = set()
    while True:
        try:
            t = now(); ws = t - t % 300
            if ws not in seen and t < ws + ENTRY - 5:
                w = discover(ws)
                if w:
                    seen.add(ws); run_window(w)
                    if len(seen) > 500: seen = set(list(seen)[-100:])
            time.sleep(3)
        except KeyboardInterrupt: print("\nparado."); break
        except Exception as ex: print("  err:", ex); time.sleep(10)

if __name__ == "__main__":
    try: sys.stdout.reconfigure(encoding="utf-8")
    except Exception: pass
    main()
