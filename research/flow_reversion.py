"""
flow_reversion.py — Refina FADE-SELL: el dump agresor grande (5m, p99) ¿SOBREPASA y REBOTA? Si sí, comprar el
dump y VENDER el rebote a los pocos s (salida temprana) bate a aguantar a resolución: capturas más del
movimiento con menos varianza. Como taker pago el spread DOS veces (compro al ask, vendo al bid), así que el
rebote tiene que superar el spread doble.

Panel 1: FORMA del rebote — mid del outcome dumpeado en la entrada y a +Hs (Δpp). ¿Sube tras el dump?
Panel 2: PnL por horizonte de salida (taker ask→bid), + baseline HOLD a resolución. train/test.

    cd ~/polymarket-btc-up-down/research && python3 flow_reversion.py
"""
import csv, os, sys, glob, json, time, bisect, urllib.request

DIR = os.path.join(os.path.dirname(__file__), "lab")
REACT = 5; BUFFER = 5; TOL = 12; WLEN = 300
PCT = 0.99                    # solo los dumps más grandes (donde flow_follow dio +EV)
HORIZONS = (5, 15, 30, 60, 120, 180)
CACHE = os.path.join(DIR, "clob_reso_mmtoxic.csv")


def get(url, tries=2):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "rev/1.0"})
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


def load_books():
    """slug -> {Up:(tss,bids,asks), Down:(...)}"""
    tmp = {}
    for path in sorted(glob.glob(os.path.join(DIR, "books_*.csv"))):
        with open(path, encoding="utf-8") as fh:
            rd = csv.reader(fh); next(rd, None)
            for row in rd:
                if len(row) < 11 or not row[1].startswith("btc-updown-5m-") or row[3] not in ("Up", "Down"):
                    continue
                try:
                    ts = int(row[0])
                    bid = float(row[4]) if row[4] else None
                    ask = float(row[10]) if row[10] else None
                except Exception: continue
                if bid is None or ask is None: continue
                tmp.setdefault(row[1], {"Up": [], "Down": []})[row[3]].append((ts, bid, ask))
    B = {}
    for slug, sides in tmp.items():
        B[slug] = {}
        for s in ("Up", "Down"):
            rows = sorted(sides[s])
            B[slug][s] = ([r[0] for r in rows], [r[1] for r in rows], [r[2] for r in rows])
    return B


def at(idx, t, which, tol=TOL):
    tss = idx[0]
    if not tss: return None
    i = bisect.bisect_left(tss, t); best = None; bd = tol + 1
    for j in (i - 1, i, i + 1):
        if 0 <= j < len(tss):
            d = abs(tss[j] - t)
            if d < bd: bd = d; best = idx[which][j]
    return best if bd <= tol else None


def load_dumps():
    seen = set(); T = []
    for path in sorted(glob.glob(os.path.join(DIR, "wintrades_*.csv"))):
        with open(path, encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                if r.get("trade_side") != "SELL" or r.get("outcome") not in ("Up", "Down"): continue
                if not (r.get("slug", "").startswith("btc-updown-5m-")): continue
                k = (r.get("tx", ""), r["outcome"], r.get("price"))
                if k in seen: continue
                seen.add(k)
                try:
                    ts = int(float(r["ts_trade"])); size = float(r["size"])
                except Exception: continue
                T.append((r["slug"], r["cid"], ts, r["outcome"], size))
    return T


def pctl(vals, q):
    s = sorted(vals); return s[min(len(s) - 1, int(q * len(s)))] if s else None


def main():
    B = load_books(); T = load_dumps()
    thr = pctl([x[4] for x in T], PCT)
    T = [x for x in T if x[4] >= thr]
    print(f"dumps SELL 5m ≥ p{int(PCT*100)} (size≥{thr:.0f}): {len(T)}")
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
        if w: reso[cid] = w
        return w

    # recolectar: entrada + serie de exits
    recs = []   # (ws, entry_ask, entry_mid, won, {H: (bid_H, mid_H)})
    for slug, cid, ts, X, size in T:
        if slug not in B: continue
        ws = int(slug.split("-")[-1]); close = ws + WLEN; te = ts + REACT
        if te > close - BUFFER: continue
        idx = B[slug][X]
        ea = at(idx, te, 2); eb = at(idx, te, 1)
        if ea is None or eb is None: continue
        emid = (ea + eb) / 2
        exits = {}
        for H in HORIZONS:
            tx = te + H
            if tx > close: continue
            b = at(idx, tx, 1); a = at(idx, tx, 2)
            if b is None or a is None: continue
            exits[H] = (b, (a + b) / 2)
        win = resolve(cid)
        won = (1 if win == X else 0) if win in ("Up", "Down") else None
        recs.append((ws, ea, emid, won, exits))
    n = len(recs)
    print(f"con libro y entrada válida: {n}")
    if not n: return

    def mean(xs): xs = [x for x in xs if x is not None]; return sum(xs) / len(xs) if xs else float("nan")
    mid = sorted(r[0] for r in recs)[n // 2]

    print("\n" + "=" * 78)
    print("  PANEL 1 — FORMA DEL REBOTE: Δmid del outcome dumpeado desde la entrada (pp)")
    print("=" * 78)
    print(f"  entrada n={n}  ·  mid entrada medio {mean([r[2] for r in recs]):.3f}")
    for H in HORIZONS:
        d = [ (r[4][H][1] - r[2]) * 100 for r in recs if H in r[4] ]
        if d: print(f"    +{H:>3}s : Δmid {mean(d):+.2f}pp   (n={len(d)})")
    print("  (si Δmid sube con H → rebota; el pico marca el mejor horizonte de salida)")

    print("\n" + "=" * 78)
    print("  PANEL 2 — PnL por salida (taker: compro ask, vendo bid; doble spread)  ·  pp")
    print("=" * 78)
    print(f"  {'salida':>12}{'n':>7}{'PnL':>8}{'PnL_tr':>9}{'PnL_te':>9}")
    for H in HORIZONS:
        sub = [r for r in recs if H in r[4]]
        if len(sub) < 30: continue
        pnls = [(r[4][H][0] - r[1] - fee(r[1]) - fee(r[4][H][0])) * 100 for r in sub]
        tr = [p for r, p in zip(sub, pnls) if r[0] < mid]; te = [p for r, p in zip(sub, pnls) if r[0] >= mid]
        print(f"  {'vender +'+str(H)+'s':>12}{len(sub):>7}{mean(pnls):>+7.2f}{mean(tr):>+9.2f}{mean(te):>+9.2f}")
    hsub = [r for r in recs if r[3] is not None]
    if hsub:
        hp = [(r[3] - r[1] - fee(r[1])) * 100 for r in hsub]
        htr = [p for r, p in zip(hsub, hp) if r[0] < mid]; hte = [p for r, p in zip(hsub, hp) if r[0] >= mid]
        print(f"  {'HOLD resol.':>12}{len(hsub):>7}{mean(hp):>+7.2f}{mean(htr):>+9.2f}{mean(hte):>+9.2f}")
    print("\nLECTURA: si algún horizonte de salida bate a HOLD con PnL + y estable (tr y te) → la reversión temprana")
    print("es mejor que aguantar (menos varianza, más edge). Si todos < HOLD o −  → el rebote no supera el doble")
    print("spread de taker y el único modo es aguantar (o ser maker en la entrada, otra liga).")


if __name__ == "__main__":
    try: sys.stdout.reconfigure(encoding="utf-8")
    except Exception: pass
    main()
