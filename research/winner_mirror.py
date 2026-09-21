"""
winner_mirror.py — ¿El edge de los wallets GANADORES es TRANSFERIBLE? Copiamos sus BUY: entramos al ask que
NOSOTROS conseguimos y aguantamos a resolución. Su ventaja es de maker; ¿su dirección sobrevive el spread?

v2 (corrige timing): la 1ª versión usó ts_seen (cuando el colector VIO el fill, con minutos de lag del
data-api) → slip salía NEGATIVO (imposible: un taker no compra más barato que el maker) porque muchos ts_seen
caían POST-cierre (ask contaminado/archivado). Aquí entro en ts_trade + REACCIÓN (5s, viable en vivo por WSS),
SOLO si es PRE-cierre con margen para llenar. Reporto el lag y el %post-cierre para exponer la contaminación,
y comparo el modelo REAL (ts_trade+react) vs el viejo (ts_seen). Neto de fee taker, por wallet y train/test.

    cd ~/polymarket-btc-up-down/research && python3 winner_mirror.py
"""
import csv, os, sys, glob, json, time, bisect, urllib.request

DIR = os.path.join(os.path.dirname(__file__), "lab")
TOL = 15
REACT = 5                    # s de reacción tras ver el trade (detección WSS + orden REST)
BUFFER = 5                   # s de margen antes del cierre para poder llenar
CACHE = os.path.join(DIR, "clob_reso_mmtoxic.csv")


def get(url, tries=2):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "mirror/2.0"})
            with urllib.request.urlopen(req, timeout=12) as r: return json.load(r)
        except Exception:
            if i == tries - 1: return None
            time.sleep(0.3)


def winner_clob(cid):
    d = get(f"https://clob.polymarket.com/markets/{cid}")
    if isinstance(d, dict):
        for t in d.get("tokens", []):
            if t.get("winner") is True: return t.get("outcome")
    return None


def fee(p): return 0.07 * p * (1 - p)


def load_books_asks():
    tmp = {}
    for path in sorted(glob.glob(os.path.join(DIR, "books_*.csv"))):
        with open(path, encoding="utf-8") as fh:
            rd = csv.reader(fh); next(rd, None)
            for row in rd:
                if len(row) < 11 or not row[1].startswith("btc-updown-") or row[3] not in ("Up", "Down"):
                    continue
                try:
                    ts = int(row[0]); ask = float(row[10]) if row[10] else None
                except Exception: continue
                if ask is None: continue
                tmp.setdefault(row[1], {"Up": [], "Down": []})[row[3]].append((ts, ask))
    B = {}
    for slug, sides in tmp.items():
        B[slug] = {}
        for s in ("Up", "Down"):
            rows = sorted(sides[s]); B[slug][s] = ([t for t, _ in rows], [a for _, a in rows])
    return B


def ask_at(idx_ask, t, tol=TOL):
    tss, asks = idx_ask
    if not tss: return None
    i = bisect.bisect_left(tss, t); best = None; bd = tol + 1
    for j in (i - 1, i, i + 1):
        if 0 <= j < len(tss):
            d = abs(tss[j] - t)
            if d < bd: bd = d; best = asks[j]
    return best if bd <= tol else None


def load_fills():
    F = []
    for path in sorted(glob.glob(os.path.join(DIR, "fills_*.csv"))):
        with open(path, encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                if r.get("trade_side") != "BUY" or r.get("outcome") not in ("Up", "Down"): continue
                try:
                    ts_seen = int(float(r["ts_seen"])); ts_trade = int(float(r["ts_trade"])); price = float(r["price"])
                except Exception: continue
                F.append((r["wallet"], ts_seen, ts_trade, r["slug"], r["cid"], r["outcome"], price))
    return F


def main():
    B = load_books_asks(); F = load_fills()
    print(f"fills BUY de ganadores: {len(F)} · slugs con libro: {len(B)}")
    reso = {}
    for fn in ("clob_reso_mmtoxic.csv", "clob_reso_mmlogs.csv", "clob_reso_mw.csv",
               "clob_reso_win.csv", "clob_reso_tape.csv", "clob_reso_uni.csv"):
        p = os.path.join(DIR, fn)
        if os.path.exists(p):
            for r in csv.DictReader(open(p, encoding="utf-8")):
                if r["winner"]: reso[r["cid"]] = r["winner"]

    def resolve(cid):
        if cid in reso: return reso[cid]
        w = winner_clob(cid); time.sleep(0.1)
        if w:
            reso[cid] = w; nf = not os.path.exists(CACHE)
            with open(CACHE, "a", newline="", encoding="utf-8") as f:
                cw = csv.writer(f)
                if nf: cw.writerow(["cid", "winner"])
                cw.writerow([cid, w])
        return w

    lags = []; post_close = 0; total = 0
    recs = []; dedup = set()
    for wallet, ts_seen, ts_trade, slug, cid, outcome, price in F:
        if slug not in B: continue
        key = (cid, outcome, round(price, 3), wallet)
        if key in dedup: continue
        dedup.add(key)
        ws = int(slug.split("-")[-1]); wlen = 300 if "-5m-" in slug else 900; close = ws + wlen
        total += 1; lags.append(ts_seen - ts_trade)
        if ts_seen > close: post_close += 1
        # modelo REAL: entrar en ts_trade+REACT, solo si pre-cierre con margen
        te = ts_trade + REACT
        if te > close - BUFFER: continue
        oa = ask_at(B[slug][outcome], te)
        if oa is None or not (0.0 < oa < 1.0): continue
        win = resolve(cid)
        if win not in ("Up", "Down"): continue
        as_ = ask_at(B[slug][outcome], ts_seen)   # viejo (ts_seen) para contraste
        recs.append((wallet, ws, 1 if win == outcome else 0, oa, price, as_))
    lags.sort()
    med = lags[len(lags) // 2] if lags else 0
    print(f"lag ts_seen−ts_trade: mediana {med}s · máx {lags[-1] if lags else 0}s · "
          f"%post-cierre (intradeable): {100*post_close/total:.0f}%  (esto contaminaba la v1)")
    n = len(recs)
    print(f"fills copiables PRE-cierre (modelo real): {n}")
    if not n: return

    def mean(xs): return sum([x for x in xs if x is not None]) / max(1, len([x for x in xs if x is not None]))
    mid = sorted(r[1] for r in recs)[n // 2]

    def block(name, sub):
        m = len(sub)
        if not m: return
        wr = 100 * mean([r[2] for r in sub]); tp = mean([r[4] for r in sub]); oa = mean([r[3] for r in sub])
        their = 100 * mean([r[2] - r[4] for r in sub])
        our = 100 * mean([r[2] - r[3] - fee(r[3]) for r in sub])
        tr = [r for r in sub if r[1] < mid]; te = [r for r in sub if r[1] >= mid]
        ourtr = 100 * mean([r[2] - r[3] - fee(r[3]) for r in tr]) if tr else float("nan")
        ourte = 100 * mean([r[2] - r[3] - fee(r[3]) for r in te]) if te else float("nan")
        print(f"  {name:>12}{m:>7}{wr:>7.0f}%{tp:>9.3f}{oa:>9.3f}{100*(oa-tp):>+8.1f}"
              f"{their:>+9.2f}{our:>+9.2f}{ourtr:>+9.2f}{ourte:>+9.2f}")

    print("\n" + "=" * 96)
    print("  MIRROR v2 (entrada ts_trade+5s, PRE-cierre) — seguirlos al ask y aguantar a resolución (neto fee)")
    print("=" * 96)
    print(f"  {'wallet':>12}{'n':>7}{'gana%':>7}{'su_prec':>9}{'ntro_ask':>9}{'slip':>8}{'su_EV':>9}{'ntro_EV':>9}{'EV_tr':>9}{'EV_te':>9}")
    for wal in sorted(set(r[0] for r in recs)):
        block(wal, [r for r in recs if r[0] == wal])
    print("  " + "-" * 94)
    block("TODOS", recs)
    old_ev = 100 * mean([r[2] - r[5] - fee(r[5]) for r in recs if r[5] is not None])
    print(f"\n  (contraste v1 con ts_seen sobre estas mismas: ntro_EV {old_ev:+.2f}pp — si difiere mucho del real, "
          f"era timing)")
    print("\nLECTURA: ahora 'slip' DEBE ser + (pagamos el spread al seguir tarde). Si 'ntro_EV' sigue + y estable")
    print("(tr y te) con slip realista → edge de dirección REAL y transferible. Si se derrumba a −/0 → la v1 era")
    print("contaminación de timing (post-cierre). OJO ejecución: seguir wallet exacta en vivo es difícil (WSS no")
    print("trae wallet); si es real, el paso siguiente es reencuadrar como señal de FLUJO agresor (sí en WSS).")


if __name__ == "__main__":
    try: sys.stdout.reconfigure(encoding="utf-8")
    except Exception: pass
    main()
