"""
EXPERIMENTO #11 — MAKER3 PAPER (DRY): el maker que llega TEMPRANO (prioridad de tiempo FIFO).

Corrección tras el error de maker2: el CLOB casa por precio+TIEMPO. maker2 posteaba a 195s = al FINAL
de la cola → solo se llenaba en desplomes (perdedores). El maker ganador postea PRONTO y deja el bid
DESCANSANDO; cuando el precio baja y lo toca, se llena EL PRIMERO (prioridad de tiempo) = caza el primer
roce = dip TRANSITORIO que rebota = gana. No es velocidad; es llegar temprano y esperar. Una Pi PUEDE.

Regla:
  · al abrir la ventana (ENTRY_EARLY=10s) posteamos un bid DESCANSANDO a TARGET (zona barata) en AMBOS
    lados (no sabemos aún cuál será el despreciado).
  · el lado que BAJE y toque TARGET nos llena (primer roce = prioridad FIFO por haber llegado temprano);
    el otro sube y nunca se ejecuta.
  · aguantar a resolución. won = el lado comprado ganó. Resolución SOLO por CLOB (Chainlink), nunca Binance.

TEST decisivo (mismo que mató a maker2): ¿el win de lo que llenamos SUPERA el precio (TARGET)? Si sí, el
early-rester caza los dips que revierten = edge ALCANZABLE desde una Pi (y maker2 fallaba por postear tarde).
Split por FASE del fill (roce temprano vs tardío) para ver el efecto del dip transitorio.

    python maker3_paper.py            # 24/7 (systemd)
    python maker3_paper.py --analyze  # veredicto
Autónomo (stdlib). Log gitignored.
"""
import urllib.request, json, time, csv, os, sys, math

ENTRY_EARLY = 10       # s: postear al ABRIR la ventana (prioridad de tiempo)
TARGET      = 0.35     # precio del bid descansando en AMBOS lados (dentro de la zona barata <40¢)
POLL        = 3
LOG = os.path.join(os.path.dirname(__file__), "maker3_paper_log.csv")
HEADER = ["ws", "slug", "side", "target", "fill_phase", "status", "winner", "won", "cid"]

def get(url, tries=2):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "maker3/1.0"})
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

def winner_clob(cid):
    d = get(f"https://clob.polymarket.com/markets/{cid}")
    if not isinstance(d, dict): return None
    for t in d.get("tokens", []):
        if t.get("winner") is True: return t.get("outcome")
    return None

def backfill_pending(verbose=False):
    """Rellena el ganador (SOLO vía CLOB) en filas pendientes. NUNCA Binance."""
    if not os.path.exists(LOG): return 0
    rows = list(csv.DictReader(open(LOG, encoding="utf-8")))
    if not rows: return 0
    n = 0
    for r in rows:
        if r.get("winner") or not r.get("cid"): continue
        w = winner_clob(r["cid"])
        if w is None: continue
        r["winner"] = w
        r["won"] = ("1" if w == r["side"] else "0") if r.get("side") else ""
        n += 1; time.sleep(0.1)
    if n:
        with open(LOG, "w", newline="", encoding="utf-8") as f:
            wr = csv.DictWriter(f, fieldnames=HEADER); wr.writeheader(); wr.writerows(rows)
    if verbose: print(f"backfill: {n} filas resueltas por CLOB")
    return n

def run_window(win):
    ws, slug, cid = win["ws"], win["slug"], win["cid"]
    print(f"\n── {slug} — resting bid {TARGET} en AMBOS lados desde {ENTRY_EARLY}s (prioridad de tiempo)")
    while now() < ws + ENTRY_EARLY: time.sleep(1)
    seen = set(); filled = None; fphase = ""
    while now() < ws + 300 and filled is None:
        feed = get(f"https://data-api.polymarket.com/trades?market={cid}&limit=100") or []
        for t in feed:
            if t.get("side") != "SELL": continue
            try:
                tp = float(t.get("price") or 1); tt = int(t.get("timestamp") or 0)
            except Exception: continue
            if tt < ws + ENTRY_EARLY: continue          # solo flujo tras postear
            if tp <= TARGET:                            # el precio tocó nuestro bid descansando
                h = (t.get("transactionHash"), tt, tp, t.get("outcome"))
                if h in seen: continue
                seen.add(h)
                filled = t.get("outcome"); fphase = tt - ws
                print(f"   FILL {filled} @ {TARGET} (primer roce a los {fphase}s, venta a {tp})"); break
        if filled: break
        time.sleep(POLL)
    status = "filled" if filled else "no_fill"

    # resolución SOLO por CLOB (Chainlink)
    while now() < ws + 300 + 5: time.sleep(3)
    win_side = None; t0 = now()
    while now() < t0 + 300 and win_side is None:
        win_side = winner_clob(cid)
        if win_side is None: time.sleep(15)
    won = "" if (win_side is None or filled is None) else (1 if win_side == filled else 0)
    log([ws, slug, filled or "", TARGET, fphase, status, win_side or "", won, cid])
    print(f"   -> {status} side={filled or '—'} phase={fphase} winner={win_side or 'PEND'} won={won}")

def analyze():
    if not os.path.exists(LOG): print("sin log"); return
    backfill_pending()
    rows = list(csv.DictReader(open(LOG, encoding="utf-8")))
    from collections import Counter
    F = [r for r in rows if r["status"] == "filled" and r["won"] in ("0", "1")]
    posted = len(rows)
    print(f"ventanas: {posted}  ·  {dict(Counter(r['status'] for r in rows))}")
    print(f"tasa de llenado: {len([r for r in rows if r['status']=='filled'])/posted:.0%}"
          f"  (bid descansando a {TARGET} en ambos lados desde {ENTRY_EARLY}s)")
    if not F: print("  (aún sin fills resueltos)"); return

    def rep(label, rs):
        n = len(rs)
        if not n: print(f"  {label:<26} sin fills"); return
        wr = sum(int(r["won"]) for r in rs) / n; ap = TARGET
        ev = wr - ap; se = math.sqrt(wr * (1 - wr) / n); rel = ev / ap * 100
        sig = "SIG" if wr - 1.96 * se > ap else ("+" if wr > ap else "")
        print(f"  {label:<26} n={n:>4}  win {wr:.1%} (IC {max(0,wr-1.96*se):.1%}-{min(1,wr+1.96*se):.1%})  "
              f"target {ap:.0%}  EDGE {ev*100:+.2f}pp  rel {rel:+.1f}% {sig}")

    print(f"\nEDGE del early-rester (win − target {TARGET:.0%}):")
    rep("TODO", F)
    print("  — por FASE del primer roce (¿los dips tempranos revierten más?):")
    for lo, hi, lab in [(0, 60, "0-60s"), (60, 120, "60-120s"), (120, 195, "120-195s"),
                        (195, 300, "195-300s"), (300, 1e9, ">300")]:
        seg = [r for r in F if r.get("fill_phase") and lo <= int(r["fill_phase"]) < hi]
        rep(lab, seg)

    print("\nVEREDICTO (¿el edge maker es ALCANZABLE llegando temprano?):")
    n = len(F); wr = sum(int(r["won"]) for r in F) / n; se = math.sqrt(wr * (1 - wr) / n)
    print("  · maker2 (posteando TARDE, 195s): filled win 9.5% a 16¢ = −6.5pp (selección adversa).")
    if n < 40:
        print(f"  → {n}/40 fills, sin veredicto aún.")
    elif wr - 1.96 * se > TARGET:
        print(f"  → EDGE +{(wr-TARGET)*100:.1f}pp SIGNIFICATIVO llegando temprano → el edge SÍ se alcanza desde")
        print("    una Pi; maker2 fallaba por postear tarde. Yo estaba equivocado al matarlo.")
    elif wr > TARGET:
        print(f"  → EDGE +{(wr-TARGET)*100:.1f}pp positivo pero no significativo — seguir.")
    else:
        print(f"  → win {wr:.1%} ≤ target {TARGET:.0%}: llegar temprano tampoco captura el edge. Ahí sí,")
        print("    hay un componente de velocidad que una Pi no vence. Negativo legítimo.")

def main():
    if "--analyze" in sys.argv: analyze(); return
    if "--resolve" in sys.argv: print(f"rellenadas {backfill_pending(verbose=True)}"); return
    print("=" * 60 + "\n  MAKER3 PAPER (DRY) — resting bid temprano, prioridad de tiempo\n" + "=" * 60)
    n = backfill_pending(verbose=True)
    if n: print(f"backfill inicial: {n}")
    seen = set(); last_bf = now()
    while True:
        try:
            t = now(); ws = t - t % 300
            if ws not in seen and t < ws + ENTRY_EARLY + 30:   # a tiempo de postear temprano
                w = discover(ws)
                if w:
                    seen.add(ws); run_window(w)
                    if len(seen) > 500: seen = set(list(seen)[-100:])
            if now() - last_bf > 600:
                nb = backfill_pending(); last_bf = now()
                if nb: print(f"backfill: {nb}")
            time.sleep(2)
        except KeyboardInterrupt: print("\nparado."); break
        except Exception as ex: print("  err:", ex); time.sleep(10)

if __name__ == "__main__":
    try: sys.stdout.reconfigure(encoding="utf-8")
    except Exception: pass
    main()
