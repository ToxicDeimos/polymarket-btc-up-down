"""
winner_criteria.py — El criterio de los ganadores está en sus GANADORAS vs PERDEDORAS (idea del usuario).
Por cada BUY de los wallets ganadores (fills_*.csv) calculo features OBSERVABLES en el instante de entrada y
busco cuáles separan sus aciertos de sus fallos.

CONTROL DE PRECIO (clave): comparar ganadas vs perdidas a pelo engaña — las compras a 0,85 ganan más que las de
0,20 solo por el precio, y todo feature ligado al precio parecería "criterio". Por eso mido contra el RESIDUO
r = ganó − precio pagado (lo que acertaron POR ENCIMA de lo que el precio ya implicaba), normalizado
z = r/√(p(1−p)). Un feature que ordena z separa sus aciertos de sus fallos A IGUALDAD DE PRECIO = criterio real.

Salida: (1) ranking de features por Spearman(feature, z), exigiendo MISMO SIGNO en train y test; (2) quintiles
de los mejores con su EV y el NUESTRO (al ask +5s, neto fee); (3) cruce 2x2 de los dos mejores; (4) top por wallet.

    cd ~/polymarket-btc-up-down/research && python3 winner_criteria.py
"""
import csv, os, sys, glob, json, time, bisect, math, urllib.request

DIR = os.path.join(os.path.dirname(__file__), "lab")
TOL = 15
CACHE = os.path.join(DIR, "clob_reso_mmtoxic.csv")
FEATS = [
    ("pos_spread", "posición en spread (0=bid/maker 1=ask/taker)"),
    ("desc_ask",   "descuento vs ask (ask − su precio)"),
    ("spread",     "spread del outcome"),
    ("imb",        "imbalance libro (fullimb)"),
    ("dep_ratio",  "log(prof. bid≤2¢ / prof. ask≤2¢)"),
    ("frac",       "fracción de ventana transcurrida"),
    ("spotfav",    "spot desde el open a favor ($)"),
    ("mom30",      "spot últimos 30s a favor ($)"),
    ("d30",        "Δmid del outcome en 30s"),
    ("d60",        "Δmid del outcome en 60s"),
    ("flow30",     "presión neta de flujo hacia X (30s)"),
    ("act30",      "nº de prints en 30s"),
    ("dd",         "drawdown desde el máx de ventana"),
    ("overround",  "suma de asks − 1"),
    ("lsize",      "log tamaño de su fill"),
    ("price",      "precio pagado (control)"),
]


def get(url, tries=2):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "crit/1.0"})
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


def load_fills():
    F = []; dd = set()
    for path in sorted(glob.glob(os.path.join(DIR, "fills_*.csv"))):
        with open(path, encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                if r.get("trade_side") != "BUY" or r.get("outcome") not in ("Up", "Down"): continue
                slug = r.get("slug", "") or ""
                if not slug.startswith("btc-updown-"): continue
                try:
                    ts = int(float(r["ts_trade"])); p = float(r["price"]); sz = float(r.get("size") or 0)
                except Exception: continue
                k = (r.get("tx", ""), r["outcome"], r.get("price"), r.get("wallet"))
                if k in dd: continue
                dd.add(k)
                F.append({"w": r["wallet"], "ts": ts, "slug": slug, "cid": r["cid"], "X": r["outcome"],
                          "p": p, "size": sz, "tx": r.get("tx", "")})
    return F


def load_books(slugs):
    tmp = {}
    for path in sorted(glob.glob(os.path.join(DIR, "books_*.csv"))):
        with open(path, encoding="utf-8") as fh:
            rd = csv.reader(fh); next(rd, None)
            for row in rd:
                if len(row) < 11 or row[1] not in slugs or row[3] not in ("Up", "Down"): continue
                try:
                    ts = int(row[0]); b = float(row[4]) if row[4] else None; a = float(row[10]) if row[10] else None
                except Exception: continue
                if b is None or a is None: continue
                tmp.setdefault(row[1], {"Up": [], "Down": []})[row[3]].append((ts, b, a))
    B = {}
    for slug, sides in tmp.items():
        B[slug] = {}
        for s in ("Up", "Down"):
            r = sorted(sides[s]); mids = [(x[1] + x[2]) / 2 for x in r]; pm = []; hi = -1.0
            for m in mids: hi = max(hi, m); pm.append(hi)
            B[slug][s] = ([x[0] for x in r], [x[1] for x in r], [x[2] for x in r], mids, pm)
    return B


def load_depth(slugs):
    tmp = {}
    for path in sorted(glob.glob(os.path.join(DIR, "bookdepth_*.csv"))):
        with open(path, encoding="utf-8") as fh:
            rd = csv.reader(fh); next(rd, None)
            for row in rd:
                if len(row) < 11 or row[1] not in slugs or row[3] not in ("Up", "Down"): continue
                try:
                    ts = int(row[0]); bd = float(row[8] or 0); ad = float(row[9] or 0)
                    im = float(row[10]) if row[10] else None
                except Exception: continue
                tmp.setdefault(row[1], {"Up": [], "Down": []})[row[3]].append((ts, bd, ad, im))
    D = {}
    for slug, sides in tmp.items():
        D[slug] = {}
        for s in ("Up", "Down"):
            r = sorted(sides[s]); D[slug][s] = ([x[0] for x in r], r)
    return D


def load_trades(cids):
    T = {}; seen = set()
    for path in sorted(glob.glob(os.path.join(DIR, "wintrades_*.csv"))):
        with open(path, encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                cid = r.get("cid")
                if cid not in cids or r.get("outcome") not in ("Up", "Down") or r.get("trade_side") not in ("BUY", "SELL"):
                    continue
                k = (r.get("tx", ""), r["outcome"], r.get("price"), r["trade_side"])
                if k in seen: continue
                seen.add(k)
                try: ts = int(float(r["ts_trade"])); sz = float(r["size"])
                except Exception: continue
                T.setdefault(cid, []).append((ts, r["trade_side"], r["outcome"], sz, r.get("tx", "")))
    out = {}
    for cid, lst in T.items():
        lst.sort(); out[cid] = ([x[0] for x in lst], lst)
    return out


def load_spot():
    out = []
    for path in sorted(glob.glob(os.path.join(DIR, "spot_*.csv"))):
        with open(path, encoding="utf-8") as fh:
            rd = csv.reader(fh); next(rd, None)
            for row in rd:
                try: out.append((int(row[0]), float(row[1])))
                except Exception: continue
    out.sort(); return [t for t, _ in out], [p for _, p in out]


def le_i(tss, t, tol=TOL):
    i = bisect.bisect_right(tss, t) - 1
    return i if (i >= 0 and t - tss[i] <= tol) else None


def rank(a):
    idx = sorted(range(len(a)), key=lambda i: a[i]); r = [0.0] * len(a); i = 0
    while i < len(idx):
        j = i
        while j + 1 < len(idx) and a[idx[j + 1]] == a[idx[i]]: j += 1
        avg = (i + j) / 2
        for k in range(i, j + 1): r[idx[k]] = avg
        i = j + 1
    return r


def pearson(x, y):
    n = len(x)
    if n < 3: return 0.0
    mx = sum(x) / n; my = sum(y) / n
    sxy = sum((a - mx) * (b - my) for a, b in zip(x, y))
    sx = math.sqrt(sum((a - mx) ** 2 for a in x)); sy = math.sqrt(sum((b - my) ** 2 for b in y))
    return sxy / (sx * sy) if sx > 0 and sy > 0 else 0.0


def spearman(x, y): return pearson(rank(x), rank(y))
def mean(xs): return sum(xs) / len(xs) if xs else float("nan")


def main():
    F = load_fills()
    slugs = set(f["slug"] for f in F); cids = set(f["cid"] for f in F)
    print(f"fills BUY ganadores: {len(F)} · ventanas: {len(slugs)}")
    B = load_books(slugs); DP = load_depth(slugs); TR = load_trades(cids); sts, spx = load_spot()
    print(f"libro: {len(B)} · depth: {len(DP)} · cinta: {len(TR)} · spot: {len(sts)}")
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
            with open(CACHE, "a", newline="", encoding="utf-8") as fo:
                cw = csv.writer(fo)
                if nf: cw.writerow(["cid", "winner"])
                cw.writerow([cid, w])
        return w

    recs = []; done = 0
    for f in F:
        done += 1
        if done % 5000 == 0: print(f"   … {done}/{len(F)}")
        slug, X, t, p = f["slug"], f["X"], f["ts"], f["p"]
        if slug not in B or not (0.02 < p < 0.98): continue
        O = "Down" if X == "Up" else "Up"
        wlen = 300 if "-5m-" in slug else 900; ws = int(slug.split("-")[-1])
        bx = B[slug][X]; bo = B[slug][O]
        i = le_i(bx[0], t)
        if i is None: continue
        bid, ask, mid = bx[1][i], bx[2][i], bx[3][i]
        j = le_i(bo[0], t); ask_o = bo[2][j] if j is not None else None
        win = resolve(f["cid"])
        if win not in ("Up", "Down"): continue
        won = 1 if win == X else 0
        k5 = le_i(bx[0], t + 5); ask5 = bx[2][k5] if k5 is not None else ask
        ft = {}
        sp = ask - bid
        ft["price"] = p; ft["spread"] = sp
        ft["pos_spread"] = (p - bid) / sp if sp > 0.0049 else None
        ft["desc_ask"] = ask - p
        ft["frac"] = (t - ws) / wlen
        ft["overround"] = (ask + ask_o - 1) if ask_o is not None else None
        ft["dd"] = bx[4][i] - mid
        i30 = le_i(bx[0], t - 30, 20); i60 = le_i(bx[0], t - 60, 20)
        ft["d30"] = (mid - bx[3][i30]) if i30 is not None else None
        ft["d60"] = (mid - bx[3][i60]) if i60 is not None else None
        sX = 1 if X == "Up" else -1
        k0 = le_i(sts, ws, 30); k1 = le_i(sts, t, 30); k30 = le_i(sts, t - 30, 30)
        ft["spotfav"] = (spx[k1] - spx[k0]) * sX if (k0 is not None and k1 is not None) else None
        ft["mom30"] = (spx[k1] - spx[k30]) * sX if (k1 is not None and k30 is not None) else None
        if slug in DP:
            dq = DP[slug][X]; q = le_i(dq[0], t)
            if q is not None:
                _, bd, ad, im = dq[1][q]
                ft["imb"] = im; ft["dep_ratio"] = math.log((bd + 1) / (ad + 1))
        if f["cid"] in TR:
            tts, tl = TR[f["cid"]]
            lo = bisect.bisect_left(tts, t - 30); hi = bisect.bisect_left(tts, t)   # estrictamente ANTES de su trade
            net = 0.0; tot = 0.0; cnt = 0
            for (_, sd, oc, sz, tx) in tl[lo:hi]:
                if tx and tx == f["tx"]: continue
                toward = (1 if oc == X else -1) * (1 if sd == "BUY" else -1)
                net += toward * sz; tot += sz; cnt += 1
            ft["flow30"] = net / tot if tot > 0 else 0.0
            ft["act30"] = cnt
        ft["lsize"] = math.log(f["size"] + 1)
        z = (won - p) / math.sqrt(p * (1 - p))
        recs.append({"w": f["w"], "ws": ws, "won": won, "p": p, "ask5": ask5, "z": z, "f": ft})
    n = len(recs)
    print(f"fills analizables: {n}")
    if not n: return
    mid_ws = sorted(r["ws"] for r in recs)[n // 2]

    def su(rows): return 100 * mean([r["won"] - r["p"] for r in rows])
    def ntro(rows): return 100 * mean([r["won"] - r["ask5"] - fee(r["ask5"]) for r in rows if 0 < r["ask5"] < 1])

    print("\n" + "=" * 88)
    print(f"  BASELINE ganadores: n={n} · gana {100*mean([r['won'] for r in recs]):.0f}% · precio medio "
          f"{mean([r['p'] for r in recs]):.3f} · su EV {su(recs):+.2f}pp · NUESTRO EV (ask+5s) {ntro(recs):+.2f}pp")
    print("=" * 88)

    # 1) ranking
    stats = []
    for k, desc in FEATS:
        rows = [r for r in recs if r["f"].get(k) is not None]
        if len(rows) < 200: continue
        tr = [r for r in rows if r["ws"] < mid_ws]; te = [r for r in rows if r["ws"] >= mid_ws]
        rho = spearman([r["f"][k] for r in rows], [r["z"] for r in rows])
        rtr = spearman([r["f"][k] for r in tr], [r["z"] for r in tr]) if len(tr) > 100 else 0.0
        rte = spearman([r["f"][k] for r in te], [r["z"] for r in te]) if len(te) > 100 else 0.0
        stable = (rtr > 0) == (rte > 0) and rtr != 0 and rte != 0
        score = min(abs(rtr), abs(rte)) if stable else 0.0
        stats.append((score, k, desc, len(rows), rho, rtr, rte, rho * math.sqrt(len(rows)), stable))
    stats.sort(reverse=True)
    print("\n  (1) QUÉ SEPARA SUS ACIERTOS DE SUS FALLOS a igualdad de precio — Spearman(feature, residuo)")
    print(f"  {'feature':>11}{'n':>7}{'ρ':>8}{'ρ_tr':>8}{'ρ_te':>8}{'t':>7}  estable  descripción")
    for sc, k, desc, m, rho, rtr, rte, tt, st in stats:
        print(f"  {k:>11}{m:>7}{rho:>+8.3f}{rtr:>+8.3f}{rte:>+8.3f}{tt:>+7.1f}  {'  sí' if st else '  no':>7}  {desc}")

    # 2) quintiles de los mejores estables
    top = [s for s in stats if s[0] > 0 and s[1] != "price"][:4]
    for sc, k, desc, *_ in top:
        rows = sorted([r for r in recs if r["f"].get(k) is not None], key=lambda r: r["f"][k])
        m = len(rows)
        print(f"\n  (2) QUINTILES · {k} — {desc}")
        print(f"    {'rango':>21}{'n':>6}{'precio':>8}{'gana%':>7}{'suEV':>8}{'ntroEV':>8}{'ntro_tr':>8}{'ntro_te':>8}")
        for q in range(5):
            sub = rows[q * m // 5:(q + 1) * m // 5]
            if not sub: continue
            lo, hi = sub[0]["f"][k], sub[-1]["f"][k]
            tr = [r for r in sub if r["ws"] < mid_ws]; te = [r for r in sub if r["ws"] >= mid_ws]
            print(f"    {f'{lo:.3g} … {hi:.3g}':>21}{len(sub):>6}{mean([r['p'] for r in sub]):>8.3f}"
                  f"{100*mean([r['won'] for r in sub]):>6.0f}%{su(sub):>+8.2f}{ntro(sub):>+8.2f}"
                  f"{ntro(tr) if tr else float('nan'):>+8.2f}{ntro(te) if te else float('nan'):>+8.2f}")

    # 3) cruce 2x2 de los dos mejores
    if len(top) >= 2:
        k1, k2 = top[0][1], top[1][1]
        rows = [r for r in recs if r["f"].get(k1) is not None and r["f"].get(k2) is not None]
        m1 = sorted(r["f"][k1] for r in rows)[len(rows) // 2]; m2 = sorted(r["f"][k2] for r in rows)[len(rows) // 2]
        s1 = 1 if top[0][4] > 0 else -1; s2 = 1 if top[1][4] > 0 else -1
        print(f"\n  (3) CRUCE {k1} × {k2} (lado 'bueno' = el que el ranking dice que mejora el residuo)")
        print(f"    {'celda':>28}{'n':>6}{'precio':>8}{'gana%':>7}{'suEV':>8}{'ntroEV':>8}{'ntro_tr':>8}{'ntro_te':>8}")
        for a in (1, 0):
            for b in (1, 0):
                def good1(r): return (r["f"][k1] > m1) == (s1 > 0)
                def good2(r): return (r["f"][k2] > m2) == (s2 > 0)
                sub = [r for r in rows if good1(r) == bool(a) and good2(r) == bool(b)]
                if len(sub) < 30: continue
                tr = [r for r in sub if r["ws"] < mid_ws]; te = [r for r in sub if r["ws"] >= mid_ws]
                lab = f"{k1} {'bueno' if a else 'malo'} · {k2} {'bueno' if b else 'malo'}"
                print(f"    {lab:>28}{len(sub):>6}{mean([r['p'] for r in sub]):>8.3f}{100*mean([r['won'] for r in sub]):>6.0f}%"
                      f"{su(sub):>+8.2f}{ntro(sub):>+8.2f}{ntro(tr) if tr else float('nan'):>+8.2f}"
                      f"{ntro(te) if te else float('nan'):>+8.2f}")

    # 4) por wallet
    print("\n  (4) TOP-3 CRITERIOS POR WALLET (ρ con el residuo; cada uno puede tener su propio criterio)")
    for wal in sorted(set(r["w"] for r in recs)):
        rw = [r for r in recs if r["w"] == wal]
        if len(rw) < 400: continue
        res = []
        for k, _ in FEATS:
            if k == "price": continue
            rows = [r for r in rw if r["f"].get(k) is not None]
            if len(rows) < 200: continue
            res.append((abs(spearman([r["f"][k] for r in rows], [r["z"] for r in rows])), k,
                        spearman([r["f"][k] for r in rows], [r["z"] for r in rows])))
        res.sort(reverse=True)
        print(f"    {wal:>12} (n={len(rw):>5}, suEV {su(rw):+.2f}, ntroEV {ntro(rw):+.2f}): "
              + " · ".join(f"{k} {rho:+.3f}" for _, k, rho in res[:3]))

    print("\nLECTURA: (1) un feature con ρ del MISMO signo en train y test (y |t|>3) es su criterio a igualdad de")
    print("precio. (2) si en su quintil 'bueno' el ntroEV (lo que NOSOTROS ganaríamos al ask) es + en tr y te,")
    print("el criterio es aprovechable. (3) el cruce dice si combinando los dos mejores se afila. Siguiente paso:")
    print("aplicar ese criterio STANDALONE en el universo (sin ellos) — si aguanta, es nuestro edge.")


if __name__ == "__main__":
    try: sys.stdout.reconfigure(encoding="utf-8")
    except Exception: pass
    main()
