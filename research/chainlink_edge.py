"""
chainlink_edge.py — ¿La información que no miramos es la FUENTE DE RESOLUCIÓN? Polymarket resuelve con Chainlink,
y todo lo anterior usó Binance como "verdad". El lab graba chainlink_*.csv (feed on-chain, proxy del stream con
el que resuelve; ~30s de cadencia) y se han visto divergencias de hasta ~$45 con Binance.

A) ¿Qué predice mejor la resolución: Chainlink (cierre vs apertura) o Binance? ¿Cuánto discrepan?
B) ¿Comprar el lado que marca Chainlink antes del cierre es +EV? Sobre todo cuando CONTRADICE a Binance.
   Entrada al ask del snapshot siguiente (≥ t+3s, pre-cierre), aguantar a resolución, neto de fee, train/test.
C) ¿Los aciertos de los ganadores (residuo ganó−precio) correlacionan con "Chainlink más a su favor que Binance"?
   Si sí, esa es su información.

Sin look-ahead: el precio Chainlink "conocido en t" es la última fila que el colector había visto con ts ≤ t.
Caveat: la web de Polymarket muestra el stream en tiempo real; nuestro proxy on-chain puede ir ~30s tarde.

    cd ~/polymarket-btc-up-down/research && python3 chainlink_edge.py
"""
import csv, os, sys, glob, json, time, bisect, math, urllib.request

DIR = os.path.join(os.path.dirname(__file__), "lab")
CACHE = os.path.join(DIR, "clob_reso_mmtoxic.csv")
DEC = {"5m": (60, 30, 15), "15m": (120, 60, 30)}


def get(url, tries=2):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "cl/1.0"})
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
def mean(xs): return sum(xs) / len(xs) if xs else float("nan")
def sgn(x): return 1 if x > 0 else (-1 if x < 0 else 0)


def load_series(prefix):
    out = []
    for path in sorted(glob.glob(os.path.join(DIR, f"{prefix}_*.csv"))):
        with open(path, encoding="utf-8") as fh:
            rd = csv.reader(fh); next(rd, None)
            for row in rd:
                try: out.append((int(row[0]), float(row[1])))
                except Exception: continue
    out.sort(); return [t for t, _ in out], [p for _, p in out]


def known(tss, pxs, t, tol):
    i = bisect.bisect_right(tss, t) - 1
    return pxs[i] if (i >= 0 and t - tss[i] <= tol) else None


def load_asks():
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
                w = tmp.setdefault(row[1], {"cid": row[2], "Up": [], "Down": []})
                w[row[3]].append((ts, ask))
    W = {}
    for slug, w in tmp.items():
        W[slug] = {"cid": w["cid"], "ws": int(slug.split("-")[-1]), "v": "5m" if "-5m-" in slug else "15m"}
        for s in ("Up", "Down"):
            r = sorted(w[s]); W[slug][s] = ([x[0] for x in r], [x[1] for x in r])
    return W


def first_ge(tss, t, tmax):
    i = bisect.bisect_left(tss, t)
    return i if (i < len(tss) and tss[i] <= tmax) else None


def load_fills():
    F = []; dd = set()
    for path in sorted(glob.glob(os.path.join(DIR, "fills_*.csv"))):
        with open(path, encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                if r.get("trade_side") != "BUY" or r.get("outcome") not in ("Up", "Down"): continue
                slug = r.get("slug", "") or ""
                if not slug.startswith("btc-updown-"): continue
                try: ts = int(float(r["ts_trade"])); p = float(r["price"])
                except Exception: continue
                k = (r.get("tx", ""), r["outcome"], r.get("price"), r.get("wallet"))
                if k in dd: continue
                dd.add(k)
                F.append((r["wallet"], ts, slug, r["cid"], r["outcome"], p))
    return F


def rank(a):
    idx = sorted(range(len(a)), key=lambda i: a[i]); r = [0.0] * len(a); i = 0
    while i < len(idx):
        j = i
        while j + 1 < len(idx) and a[idx[j + 1]] == a[idx[i]]: j += 1
        for k in range(i, j + 1): r[idx[k]] = (i + j) / 2
        i = j + 1
    return r


def spearman(x, y):
    if len(x) < 30: return 0.0
    rx, ry = rank(x), rank(y); n = len(rx); mx = sum(rx) / n; my = sum(ry) / n
    sxy = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    sx = math.sqrt(sum((a - mx) ** 2 for a in rx)); sy = math.sqrt(sum((b - my) ** 2 for b in ry))
    return sxy / (sx * sy) if sx > 0 and sy > 0 else 0.0


def main():
    cts, cpx = load_series("chainlink"); bts, bpx = load_series("spot")
    W = load_asks()
    print(f"chainlink filas: {len(cts)} · spot: {len(bts)} · ventanas: {len(W)}")
    if not cts: print("sin datos chainlink"); return
    print(f"chainlink desde {time.strftime('%Y-%m-%d', time.gmtime(cts[0]))} hasta {time.strftime('%Y-%m-%d', time.gmtime(cts[-1]))}")
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

    # ---------- A y B ----------
    A = {"5m": [], "15m": []}
    Bd = {(v, D): [] for v in DEC for D in DEC[v]}
    done = 0
    for slug, w in W.items():
        v = w["v"]; wlen = 300 if v == "5m" else 900; ws = w["ws"]; close = ws + wlen
        c0 = known(cts, cpx, ws + 2, 90); b0 = known(bts, bpx, ws + 2, 20)
        if c0 is None or b0 is None: continue
        done += 1
        if done % 4000 == 0: print(f"   … {done}")
        win = resolve(w["cid"])
        if win not in ("Up", "Down"): continue
        wsign = 1 if win == "Up" else -1
        c1 = known(cts, cpx, close + 3, 90); b1 = known(bts, bpx, close + 3, 20)
        if c1 is not None and b1 is not None:
            A[v].append((sgn(c1 - c0), sgn(b1 - b0), wsign))
        for D in DEC[v]:
            t = close - D
            ct = known(cts, cpx, t, 90); bt = known(bts, bpx, t, 20)
            if ct is None or bt is None: continue
            cl, bl = sgn(ct - c0), sgn(bt - b0)
            if cl == 0 or bl == 0: continue
            rec = {"ws": ws, "cl": cl, "bl": bl, "w": wsign}
            for side_name, s in (("CL", cl), ("BN", bl)):
                X = "Up" if s > 0 else "Down"
                tss, asks = w[X]
                k = first_ge(tss, t + 3, close - 2)
                rec[side_name] = asks[k] if (k is not None and 0 < asks[k] < 1) else None
            Bd[(v, D)].append(rec)

    print("\n" + "=" * 86)
    print("  A) ¿QUÉ PREDICE LA RESOLUCIÓN? (signo cierre−apertura vs ganador real)")
    print("=" * 86)
    for v in ("5m", "15m"):
        rows = [r for r in A[v] if r[0] != 0 and r[1] != 0]
        if not rows: continue
        acl = 100 * mean([1 if r[0] == r[2] else 0 for r in rows])
        abn = 100 * mean([1 if r[1] == r[2] else 0 for r in rows])
        dis = [r for r in rows if r[0] != r[1]]
        cl_ok = 100 * mean([1 if r[0] == r[2] else 0 for r in dis]) if dis else float("nan")
        print(f"  {v}: n={len(rows)} · acierto Chainlink {acl:.1f}% · Binance {abn:.1f}% · discrepan {100*len(dis)/len(rows):.1f}% "
              f"(n={len(dis)}) → en las discrepancias gana Chainlink el {cl_ok:.0f}%")

    print("\n" + "=" * 86)
    print("  B) COMPRAR EL LADO DE CHAINLINK antes del cierre (al ask siguiente, aguantar, neto fee)")
    print("=" * 86)
    print(f"  {'mercado·T':>10}{'caso':>18}{'n':>7}{'ask':>7}{'gana%':>7}{'EV':>8}{'EV_tr':>8}{'EV_te':>8}")
    for v in ("5m", "15m"):
        for D in DEC[v]:
            rows = Bd[(v, D)]
            if len(rows) < 30: continue
            mid = sorted(r["ws"] for r in rows)[len(rows) // 2]
            def show(lab, sub, key, sign_key):
                sub = [r for r in sub if r[key] is not None]
                if len(sub) < 20:
                    print(f"  {f'{v}·T-{D}':>10}{lab:>18}{len(sub):>7}   (pocos)"); return
                def ev(s): return 100 * mean([(1 if r[sign_key] == r["w"] else 0) - r[key] - fee(r[key]) for r in s])
                tr = [r for r in sub if r["ws"] < mid]; te = [r for r in sub if r["ws"] >= mid]
                print(f"  {f'{v}·T-{D}':>10}{lab:>18}{len(sub):>7}{mean([r[key] for r in sub]):>7.3f}"
                      f"{100*mean([1 if r[sign_key]==r['w'] else 0 for r in sub]):>6.0f}%{ev(sub):>+8.2f}"
                      f"{ev(tr) if tr else float('nan'):>+8.2f}{ev(te) if te else float('nan'):>+8.2f}")
            dis = [r for r in rows if r["cl"] != r["bl"]]
            show("lado CL (todas)", rows, "CL", "cl")
            show("lado BN (todas)", rows, "BN", "bl")
            show("CL si discrepan", dis, "CL", "cl")
            show("BN si discrepan", dis, "BN", "bl")

    # ---------- C ----------
    F = load_fills()
    zs = []
    for wal, t, slug, cid, X, p in F:
        if not (0.02 < p < 0.98): continue
        ws = int(slug.split("-")[-1])
        c0 = known(cts, cpx, ws + 2, 90); b0 = known(bts, bpx, ws + 2, 20)
        ct = known(cts, cpx, t, 90); bt = known(bts, bpx, t, 20)
        if None in (c0, b0, ct, bt): continue
        win = resolve(cid)
        if win not in ("Up", "Down"): continue
        sX = 1 if X == "Up" else -1
        clf = (ct - c0) * sX; bnf = (bt - b0) * sX
        z = ((1 if win == X else 0) - p) / math.sqrt(p * (1 - p))
        zs.append((wal, ws, clf, bnf, clf - bnf, z))
    print("\n" + "=" * 86)
    print("  C) ¿SUS ACIERTOS SIGUEN A CHAINLINK? Spearman con el residuo (ganó − precio)")
    print("=" * 86)
    if len(zs) < 100:
        print("  pocos fills con chainlink"); return
    mid = sorted(r[1] for r in zs)[len(zs) // 2]
    def line(lab, rows):
        if len(rows) < 100: return
        tr = [r for r in rows if r[1] < mid]; te = [r for r in rows if r[1] >= mid]
        out = f"  {lab:>14} n={len(rows):>6} "
        for nm, i in (("CL a favor", 2), ("BN a favor", 3), ("CL−BN", 4)):
            out += f"· {nm} {spearman([r[i] for r in rows], [r[5] for r in rows]):+.3f}" \
                   f" (tr {spearman([r[i] for r in tr], [r[5] for r in tr]):+.3f}/te {spearman([r[i] for r in te], [r[5] for r in te]):+.3f}) "
        print(out)
    line("TODOS", zs)
    for wal in sorted(set(r[0] for r in zs)):
        line(wal, [r for r in zs if r[0] == wal])
    print("\nLECTURA: A) si en las discrepancias gana Chainlink casi siempre, resuelve con Chainlink. B) si 'CL si")
    print("discrepan' tiene EV + en tr y te → el mercado sigue a Binance y Chainlink manda: edge accionable desde la")
    print("Pi. C) si 'CL−BN' correlaciona + con el residuo de un ganador (tr y te), esa es su información.")


if __name__ == "__main__":
    try: sys.stdout.reconfigure(encoding="utf-8")
    except Exception: pass
    main()
