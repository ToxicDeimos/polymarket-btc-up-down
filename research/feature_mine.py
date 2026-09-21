"""
feature_mine.py — Destila la SELECCIÓN de los ganadores (objetivo original del proyecto). El mirror mostró que
copiar sus BUY es +1,5-2,6pp, pero el mecanismo observable se resiste (no es precio ni flujo bruto). Aquí, por
cada fill ganador, calculo features OBSERVABLES del LIBRO/spot en la entrada y mido, por bucket de cada feature,
el EV de comprar al ask (+5s, pre-cierre) y aguantar a resolución. Busco features cuyo bucket suba el EV de
forma ESTABLE (train y test) → esa es la firma observable que podríamos replicar SIN su identidad.

Features: nivel de precio del outcome comprado · imbalance de libro (fullimb, bookdepth) · profundidad a ≤2¢ ·
spread · fracción de la ventana transcurrida · acuerdo con el movimiento del spot desde el open.

    cd ~/polymarket-btc-up-down/research && python3 feature_mine.py
"""
import csv, os, sys, glob, json, time, bisect, urllib.request

DIR = os.path.join(os.path.dirname(__file__), "lab")
REACT = 5; BUFFER = 5; TOL = 15
CACHE = os.path.join(DIR, "clob_reso_mmtoxic.csv")


def get(url, tries=2):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "feat/1.0"})
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


def load_series(prefix, cols):
    """slug -> side -> ([ts], [tuple(cols)]) ordenado. cols = índices a extraer (float)."""
    tmp = {}
    for path in sorted(glob.glob(os.path.join(DIR, f"{prefix}_*.csv"))):
        with open(path, encoding="utf-8") as fh:
            rd = csv.reader(fh); next(rd, None)
            for row in rd:
                if len(row) <= max(cols) or not row[1].startswith("btc-updown-") or row[3] not in ("Up", "Down"):
                    continue
                try:
                    ts = int(row[0]); vals = tuple(float(row[c]) if row[c] else None for c in cols)
                except Exception: continue
                tmp.setdefault(row[1], {"Up": [], "Down": []})[row[3]].append((ts, vals))
    S = {}
    for slug, sides in tmp.items():
        S[slug] = {}
        for s in ("Up", "Down"):
            rows = sorted(sides[s]); S[slug][s] = ([t for t, _ in rows], [v for _, v in rows])
    return S


def load_spot():
    out = []
    for path in sorted(glob.glob(os.path.join(DIR, "spot_*.csv"))):
        with open(path, encoding="utf-8") as fh:
            rd = csv.reader(fh); next(rd, None)
            for row in rd:
                try: out.append((int(row[0]), float(row[1])))
                except Exception: continue
    out.sort(); return [t for t, _ in out], [p for _, p in out]


def le(tss, vals, t, tol=TOL):
    if not tss: return None
    i = bisect.bisect_right(tss, t) - 1
    if i >= 0 and t - tss[i] <= tol: return vals[i]
    return None


def load_fills():
    F = []; dd = set()
    for path in sorted(glob.glob(os.path.join(DIR, "fills_*.csv"))):
        with open(path, encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                if r.get("trade_side") != "BUY" or r.get("outcome") not in ("Up", "Down"): continue
                try: ts = int(float(r["ts_trade"]))
                except Exception: continue
                k = (r.get("tx", ""), r["outcome"], r.get("price"))
                if k in dd: continue
                dd.add(k)
                F.append((ts, r["slug"], r["cid"], r["outcome"]))
    return F


def main():
    BK = load_series("books", [4, 10])           # bid, ask
    BD = load_series("bookdepth", [8, 9, 10])     # bdepth2c, adepth2c, fullimb
    sts, spx = load_spot()
    F = load_fills()
    print(f"fills BUY: {len(F)} · slugs libro: {len(BK)} · slugs depth: {len(BD)} · spot: {len(sts)}")
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

    recs = []
    for ts, slug, cid, X in F:
        if slug not in BK: continue
        wlen = 300 if "-5m-" in slug else 900; ws = int(slug.split("-")[-1]); close = ws + wlen
        te = ts + REACT
        if te > close - BUFFER: continue
        bk = le(BK[slug][X][0], BK[slug][X][1], te)   # (bid, ask) al entrar
        if not bk or bk[1] is None or not (0.0 < bk[1] < 1.0): continue
        win = resolve(cid)
        if win not in ("Up", "Down"): continue
        ask = bk[1]; bid = bk[0]
        hit = 1 if win == X else 0
        f_frac = (ts - ws) / wlen
        f_spread = (ask - bid) if (bid is not None) else None
        depth = le(BD.get(slug, {}).get(X, ([], []))[0], BD.get(slug, {}).get(X, ([], []))[1], ts) if slug in BD else None
        f_imb = depth[2] if depth else None
        sp0 = le(sts, spx, ws); sp1 = le(sts, spx, ts)
        f_agree = None
        if sp0 is not None and sp1 is not None:
            sm = sp1 - sp0
            f_agree = 1 if ((X == "Up" and sm > 0) or (X == "Down" and sm < 0)) else 0
        recs.append({"ws": ws, "ask": ask, "hit": hit, "asklvl": ask, "spread": f_spread,
                     "imb": f_imb, "frac": f_frac, "agree": f_agree})
    n = len(recs)
    print(f"fills con libro+resolución: {n}")
    if not n: return

    def mean(xs): return sum(xs) / len(xs) if xs else float("nan")
    allws = sorted(r["ws"] for r in recs); mid = allws[n // 2]

    def report(name, key, buckets):
        print(f"\n  ── {name} " + "─" * (60 - len(name)))
        print(f"    {'bucket':>12}{'n':>7}{'gana%':>7}{'ask':>7}{'EV':>8}{'EV_tr':>8}{'EV_te':>8}")
        for lab, lo, hi in buckets:
            sub = [r for r in recs if r[key] is not None and lo <= r[key] < hi]
            m = len(sub)
            if m < 30:
                print(f"    {lab:>12}{m:>7}   (pocos)"); continue
            wr = 100 * mean([r["hit"] for r in sub]); ask = mean([r["ask"] for r in sub])
            ev = 100 * mean([r["hit"] - r["ask"] - fee(r["ask"]) for r in sub])
            tr = [r for r in sub if r["ws"] < mid]; te = [r for r in sub if r["ws"] >= mid]
            evtr = 100 * mean([r["hit"] - r["ask"] - fee(r["ask"]) for r in tr]) if tr else float("nan")
            evte = 100 * mean([r["hit"] - r["ask"] - fee(r["ask"]) for r in te]) if te else float("nan")
            print(f"    {lab:>12}{m:>7}{wr:>6.0f}%{ask:>7.3f}{ev:>+7.2f}{evtr:>+8.2f}{evte:>+8.2f}")

    base = 100 * mean([r["hit"] - r["ask"] - fee(r["ask"]) for r in recs])
    print("\n" + "=" * 78)
    print(f"  MINERÍA DE FEATURES en las entradas ganadoras · BASELINE (todas): EV {base:+.2f}pp (n={n})")
    print("=" * 78)
    report("NIVEL DE PRECIO (ask del outcome)", "asklvl",
           [("<0.40", 0, 0.40), ("0.40-0.60", 0.40, 0.60), ("0.60-0.80", 0.60, 0.80), (">=0.80", 0.80, 1.01)])
    report("IMBALANCE LIBRO (fullimb de X)", "imb",
           [("<0.40", 0, 0.40), ("0.40-0.55", 0.40, 0.55), ("0.55-0.70", 0.55, 0.70), (">=0.70", 0.70, 1.01)])
    report("SPREAD (ask-bid)", "spread",
           [("<0.02", 0, 0.02), ("0.02-0.04", 0.02, 0.04), ("0.04-0.08", 0.04, 0.08), (">=0.08", 0.08, 1.01)])
    report("FRACCIÓN VENTANA", "frac",
           [("<0.25", 0, 0.25), ("0.25-0.50", 0.25, 0.50), ("0.50-0.75", 0.50, 0.75), (">=0.75", 0.75, 1.01)])
    report("ACUERDO CON SPOT", "agree", [("no-acuerdo", -0.5, 0.5), ("acuerdo", 0.5, 1.5)])
    print("\nLECTURA: busco un bucket con EV claramente > BASELINE y + en train Y test → firma observable de la")
    print("selección ganadora (replicable sin su identidad). Si ningún feature separa → su edge es privado.")


if __name__ == "__main__":
    try: sys.stdout.reconfigure(encoding="utf-8")
    except Exception: pass
    main()
