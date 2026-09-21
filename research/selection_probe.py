"""
selection_probe.py — CAZAR la selección de los ganadores por CONTROL EMPAREJADO. Si su edge no aparece en
ningún feature suelto vía EV, al menos veamos QUÉ es sistemáticamente distinto cuando ELLOS entran vs un
momento AL AZAR de la misma ventana/outcome. La(s) feature(s) que más difieran = su firma de selección.

Por cada fill ganador (BUY X en ts) y un control aleatorio (mismo slug/X, otro instante), calculo:
  ask (nivel de precio) · fullimb (imbalance) · spread · drawdown (máx mid de la ventana − mid ahora, cuánto
  por debajo del techo compran) · spotfav (spot(t)−spot(ws) a favor de X: >0 el spot apoya X).
Reporto media en entradas vs controles y la separación d = (m_ent − m_ctrl)/std. |d| grande = seleccionan por
esa feature. (Descriptivo: revela su criterio aunque el mercado ya lo cotize.)

    cd ~/polymarket-btc-up-down/research && python3 selection_probe.py
"""
import csv, os, sys, glob, bisect, random

DIR = os.path.join(os.path.dirname(__file__), "lab")
TOL = 12; BUFFER = 5
random.seed(7)


def load_mids():
    tmp = {}
    for path in sorted(glob.glob(os.path.join(DIR, "books_*.csv"))):
        with open(path, encoding="utf-8") as fh:
            rd = csv.reader(fh); next(rd, None)
            for row in rd:
                if len(row) < 11 or not row[1].startswith("btc-updown-") or row[3] not in ("Up", "Down"):
                    continue
                try:
                    ts = int(row[0])
                    bid = float(row[4]) if row[4] else None
                    ask = float(row[10]) if row[10] else None
                except Exception: continue
                if bid is None or ask is None: continue
                tmp.setdefault(row[1], {"Up": [], "Down": []})[row[3]].append((ts, bid, ask))
    S = {}
    for slug, sides in tmp.items():
        S[slug] = {}
        for s in ("Up", "Down"):
            r = sorted(sides[s]); mids = [(b + a) / 2 for _, b, a in r]
            pm = []; hi = -1
            for m in mids: hi = max(hi, m); pm.append(hi)
            S[slug][s] = ([x[0] for x in r], [x[1] for x in r], [x[2] for x in r], mids, pm)
    return S


def load_imb():
    tmp = {}
    for path in sorted(glob.glob(os.path.join(DIR, "bookdepth_*.csv"))):
        with open(path, encoding="utf-8") as fh:
            rd = csv.reader(fh); next(rd, None)
            for row in rd:
                if len(row) < 11 or not row[1].startswith("btc-updown-") or row[3] not in ("Up", "Down"):
                    continue
                try:
                    ts = int(row[0]); im = float(row[10]) if row[10] else None
                except Exception: continue
                if im is None: continue
                tmp.setdefault(row[1], {"Up": [], "Down": []})[row[3]].append((ts, im))
    S = {}
    for slug, sides in tmp.items():
        S[slug] = {}
        for s in ("Up", "Down"):
            r = sorted(sides[s]); S[slug][s] = ([t for t, _ in r], [v for _, v in r])
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


def le_idx(tss, t, tol=TOL):
    i = bisect.bisect_right(tss, t) - 1
    return i if (i >= 0 and t - tss[i] <= tol) else None


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
                F.append((ts, r["slug"], r["outcome"]))
    return F


def main():
    M = load_mids(); IMB = load_imb(); sts, spx = load_spot(); F = load_fills()
    print(f"slugs libro: {len(M)} · slugs imb: {len(IMB)} · fills: {len(F)}")

    def feats(slug, X, t):
        if slug not in M: return None
        tss, bids, asks, mids, pm = M[slug][X]
        i = le_idx(tss, t)
        if i is None or asks[i] is None: return None
        ask = asks[i]; bid = bids[i]; mid = mids[i]
        dd = pm[i] - mid
        im = None
        if slug in IMB:
            j = le_idx(IMB[slug][X][0], t)
            if j is not None: im = IMB[slug][X][1][j]
        k0 = le_idx(sts, int(slug.split("-")[-1])); k1 = le_idx(sts, t)
        sf = (spx[k1] - spx[k0]) * (1 if X == "Up" else -1) if (k0 is not None and k1 is not None) else None
        return {"ask": ask, "spread": ask - bid, "dd": dd, "imb": im, "spotfav": sf}

    E = {k: [] for k in ("ask", "spread", "dd", "imb", "spotfav")}
    C = {k: [] for k in ("ask", "spread", "dd", "imb", "spotfav")}
    for ts, slug, X in F:
        wlen = 300 if "-5m-" in slug else 900; ws = int(slug.split("-")[-1]); close = ws + wlen
        fe = feats(slug, X, ts)
        if not fe: continue
        tc = random.randint(ws + 30, close - BUFFER)
        fc = feats(slug, X, tc)
        if not fc: continue
        for k in E:
            if fe[k] is not None: E[k].append(fe[k])
            if fc[k] is not None: C[k].append(fc[k])
    def mean(xs): return sum(xs) / len(xs) if xs else float("nan")
    def std(xs):
        if len(xs) < 2: return 0.0
        m = mean(xs); return (sum((x - m) ** 2 for x in xs) / len(xs)) ** 0.5

    print(f"\nfills usados: {len(E['ask'])}")
    print("=" * 74)
    print("  SELECCIÓN DE LOS GANADORES vs CONTROL aleatorio (misma ventana/outcome)")
    print("=" * 74)
    print(f"  {'feature':>10}{'entrada':>11}{'control':>11}{'dif':>10}{'d(sep)':>9}")
    labels = {"ask": "precio", "spread": "spread", "dd": "drawdown", "imb": "imbalance", "spotfav": "spot_a_favor"}
    rows = []
    for k in ("ask", "imb", "spread", "dd", "spotfav"):
        me = mean(E[k]); mc = mean(C[k]); sd = std(E[k] + C[k])
        d = (me - mc) / sd if sd else 0.0
        rows.append((abs(d), k, me, mc, d))
        print(f"  {labels[k]:>10}{me:>11.3f}{mc:>11.3f}{me-mc:>+10.3f}{d:>+9.2f}")
    rows.sort(reverse=True)
    top = rows[0]
    print(f"\nLECTURA: |d(sep)| grande = seleccionan fuerte por esa feature. Mayor separador: '{labels[top[1]]}' "
          f"(d={top[4]:+.2f}).")
    print("Interpretar el signo: p.ej. drawdown + = compran más abajo del techo; spot_a_favor + = con el spot")
    print("de su lado; imbalance + = con el libro apoyándoles. Eso es su CRITERIO. Luego habría que ver si ese")
    print("criterio, afinado, da +EV standalone (si el mercado no lo cotiza ya) o si es puro timing de fill.")


if __name__ == "__main__":
    try: sys.stdout.reconfigure(encoding="utf-8")
    except Exception: pass
    main()
