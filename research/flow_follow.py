"""
flow_follow.py — ¿El +EV de copiar a los ganadores generaliza al FLUJO OBSERVABLE (lo que el WSS SÍ da en
tiempo real: prints agresores con lado y tamaño), sin conocer la wallet? Si sí → TRADEABLE desde la Pi.

Sobre wintrades_*.csv (cinta agresora completa por ventana). Para cada print GRANDE (size ≥ percentil):
  FOLLOW: si es BUY de X → compro X (sigo al agresor comprador)
  FADE  : si es SELL de X → compro X (fadeo al agresor vendedor, capturo el overshoot)
Entrada al ask (books) en ts_trade+5s, PRE-cierre; aguanto a resolución. Neto de fee taker, con train/test.
Barre el umbral de tamaño (p90/p95/p99). Separo 5m y 15m.

    cd ~/polymarket-btc-up-down/research && python3 flow_follow.py
"""
import csv, os, sys, glob, json, time, bisect, urllib.request

DIR = os.path.join(os.path.dirname(__file__), "lab")
REACT = 5; BUFFER = 5; TOL = 15
PCTS = (0.90, 0.95, 0.99)
CACHE = os.path.join(DIR, "clob_reso_mmtoxic.csv")


def get(url, tries=2):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "flow/1.0"})
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


def load_wintrades():
    seen = set(); T = []
    for path in sorted(glob.glob(os.path.join(DIR, "wintrades_*.csv"))):
        with open(path, encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                if r.get("outcome") not in ("Up", "Down") or r.get("trade_side") not in ("BUY", "SELL"):
                    continue
                tx = r.get("tx", "")
                k = (tx, r["outcome"], r.get("price"), r["trade_side"])
                if k in seen: continue
                seen.add(k)
                try:
                    ts = int(float(r["ts_trade"])); size = float(r["size"])
                except Exception: continue
                T.append((r["slug"], r["cid"], ts, r["trade_side"], r["outcome"], size))
    return T


def pctl(vals, q):
    if not vals: return None
    s = sorted(vals); return s[min(len(s) - 1, int(q * len(s)))]


def main():
    B = load_books_asks(); T = load_wintrades()
    print(f"prints agresores (dedup): {len(T)} · slugs con libro: {len(B)}")
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

    # recolectar prints con ask pre-cierre + resolución: (v, side, size, ws, ask, hit)
    recs = []; done = 0
    for slug, cid, ts, side, outcome, size in T:
        if slug not in B: continue
        v = "5m" if "-5m-" in slug else "15m"; wlen = 300 if v == "5m" else 900
        ws = int(slug.split("-")[-1]); te = ts + REACT
        if te > ws + wlen - BUFFER: continue
        oa = ask_at(B[slug][outcome], te)
        if oa is None or not (0.0 < oa < 1.0): continue
        win = resolve(cid); done += 1
        if done % 5000 == 0: print(f"   … {done}")
        if win not in ("Up", "Down"): continue
        recs.append((v, side, size, ws, oa, 1 if win == outcome else 0))
    print(f"prints con ask pre-cierre y resolución: {len(recs)}")
    if not recs: return

    def mean(xs): return sum(xs) / len(xs) if xs else float("nan")
    for v in ("5m", "15m"):
        vv = [r for r in recs if r[0] == v]
        if not vv: continue
        sizes = [r[2] for r in vv]; mid = sorted(r[3] for r in vv)[len(vv) // 2]
        thr = {q: pctl(sizes, q) for q in PCTS}
        print("\n" + "=" * 90)
        print(f"  FLUJO {v} — seguir BUY grande / fadear SELL grande, comprar el outcome y aguantar (neto fee)")
        print("=" * 90)
        print(f"  {'señal':>10}{'pctl':>6}{'thr':>8}{'n':>7}{'ask':>7}{'gana%':>7}{'EV':>8}{'EV_tr':>8}{'EV_te':>8}")
        for label, want in (("FOLLOW-BUY", "BUY"), ("FADE-SELL", "SELL")):
            for q in PCTS:
                sub = [r for r in vv if r[1] == want and r[2] >= thr[q]]
                m = len(sub)
                if m < 30:
                    print(f"  {label:>10}{int(q*100):>5}%{thr[q]:>8.0f}{m:>7}   (pocos)"); continue
                ask = mean([r[4] for r in sub]); wr = 100 * mean([r[5] for r in sub])
                ev = 100 * mean([r[5] - r[4] - fee(r[4]) for r in sub])
                tr = [r for r in sub if r[3] < mid]; te = [r for r in sub if r[3] >= mid]
                evtr = 100 * mean([r[5] - r[4] - fee(r[4]) for r in tr]) if tr else float("nan")
                evte = 100 * mean([r[5] - r[4] - fee(r[4]) for r in te]) if te else float("nan")
                print(f"  {label:>10}{int(q*100):>5}%{thr[q]:>8.0f}{m:>7}{ask:>7.3f}{wr:>6.0f}%"
                      f"{ev:>+8.2f}{evtr:>+8.2f}{evte:>+8.2f}")
    print("\nLECTURA: si FOLLOW-BUY (o FADE-SELL) tiene EV + y ESTABLE (tr y te) a algún umbral → el edge de los")
    print("ganadores VIENE por flujo observable → TRADEABLE por WSS desde la Pi (el WSS da lado+precio en vivo).")
    print("Si el flujo genérico es ~0/− pero el mirror era +  → el edge es específico de esas wallets (necesita")
    print("rastreo on-chain, difícil). El umbral robusto es el que va + en train y test, no el de mayor EV.")


if __name__ == "__main__":
    try: sys.stdout.reconfigure(encoding="utf-8")
    except Exception: pass
    main()
