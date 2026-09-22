"""
twap_edge.py — El mercado resuelve por TWAP de Chainlink desde el 7-ago-2026 (5m: TWAP-30s del 7 al 13-ago,
TWAP-60s desde el 14-ago; 15m: TWAP-60s desde el 7-ago). Todos los análisis de cierre anteriores usaron "spot
ahora vs apertura" = la regla VIEJA (de ahí la asimetría train/test que parecía un bug).

PARTE 1 — ¿Qué definición reproduce la resolución? Con el spot de Binance (proxy de Chainlink), por régimen:
  D1 puntual:          spot(cierre) ≥ spot(inicio)
  D2 TWAP_fin/pto:     media[cierre−L, cierre] ≥ spot(inicio)
  D3 TWAP_fin/TWAP_ini: media[cierre−L, cierre] ≥ media[inicio−L, inicio]
  D4 TWAP_ventana/pto: media[inicio, cierre] ≥ spot(inicio)
  La que más acierta en un régimen es su regla real (con proxy Binance, ~93-97% ya es "la buena").

PARTE 2 — ¿Hay edge calculando el TWAP parcial? A x s del cierre parte del promedio ya es conocida:
  esperado = (media conocida·(L−x) + precio actual·x)/L ; margen = esperado − referencia (según la mejor D).
  Comprar el lado que marca el TWAP al ask FRESCO (≤3s tras decidir), aguantar a resolución, neto de fee.
  Frente a "spot-líder" (regla vieja) y en los casos donde TWAP-líder y spot-líder DISCREPAN — ahí estaría el
  dinero si el mercado mira el precio actual. tr/te dentro del régimen y semanas positivas.

    cd ~/polymarket-btc-up-down/research && python3 twap_edge.py
"""
import csv, os, sys, glob, json, time, bisect, urllib.request

DIR = os.path.join(os.path.dirname(__file__), "lab")
CACHE = os.path.join(DIR, "clob_reso_mmtoxic.csv")
T_TWAP = 1786060800      # 2026-08-07 00:00 UTC
T_5M60 = 1786665600      # 2026-08-14 00:00 UTC
XS = {"5m": (30, 15, 8), "15m": (45, 30, 15)}
MB = [("<$5", 0, 5), ("$5-15", 5, 15), ("$15-40", 15, 40), (">$40", 40, 1e9)]


def regime(v, ws):
    if ws < T_TWAP: return "puntual", 60
    if v == "5m" and ws < T_5M60: return "TWAP30", 30
    return "TWAP60", 60


def get(url, tries=2):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "twap/1.0"})
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
def week(ts): return time.strftime("%Y-%U", time.gmtime(ts))


def load_spot():
    out = []
    for path in sorted(glob.glob(os.path.join(DIR, "spot_*.csv"))):
        with open(path, encoding="utf-8") as fh:
            rd = csv.reader(fh); next(rd, None)
            for row in rd:
                try: out.append((int(row[0]), float(row[1])))
                except Exception: continue
    out.sort(); return [t for t, _ in out], [p for _, p in out]


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


def main():
    sts, spx = load_spot(); W = load_asks()
    print(f"ventanas: {len(W)} · spot: {len(sts)}")

    def avg(a, b):
        lo = bisect.bisect_left(sts, a); hi = bisect.bisect_right(sts, b)
        if hi - lo < 2: return None
        seg = spx[lo:hi]; return sum(seg) / len(seg)

    def near(t, tol):
        i = bisect.bisect_left(sts, t); best = None; bd = tol + 1
        for j in (i - 1, i):
            if 0 <= j < len(sts):
                d = abs(sts[j] - t)
                if d < bd: bd = d; best = spx[j]
        return best if bd <= tol else None

    def le(t, tol):
        i = bisect.bisect_right(sts, t) - 1
        return spx[i] if (i >= 0 and t - sts[i] <= tol) else None

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

    # ---------------- PARTE 1 ----------------
    acc = {}   # (v, reg) -> {D: [aciertos]}
    base = {}  # slug -> datos para parte 2
    done = 0
    for slug, w in W.items():
        v = w["v"]; wlen = 300 if v == "5m" else 900; ws = w["ws"]; close = ws + wlen
        reg, L = regime(v, ws)
        s0 = near(ws, 6); s1 = near(close, 6)
        if s0 is None or s1 is None: continue
        done += 1
        if done % 4000 == 0: print(f"   … {done}")
        win = resolve(w["cid"])
        if win not in ("Up", "Down"): continue
        wsgn = 1 if win == "Up" else -1
        tend = avg(close - L, close); tini = avg(ws - L, ws); tall = avg(ws, close)
        D = {"D1": s1 - s0,
             "D2": (tend - s0) if tend is not None else None,
             "D3": (tend - tini) if (tend is not None and tini is not None) else None,
             "D4": (tall - s0) if tall is not None else None}
        a = acc.setdefault((v, reg), {k: [] for k in D})
        for k, val in D.items():
            if val is None or sgn(val) == 0: continue
            a[k].append(1 if sgn(val) == wsgn else 0)
        base[slug] = (s0, tini, wsgn)

    print("\n" + "=" * 84)
    print("  PARTE 1 — ¿QUÉ REGLA REPRODUCE LA RESOLUCIÓN? (acierto con spot Binance como proxy)")
    print("=" * 84)
    print(f"  {'mercado·régimen':>18}{'n':>7}{'D1 puntual':>12}{'D2 TWAPfin/pto':>16}{'D3 TWAPfin/ini':>16}{'D4 TWAPvent':>13}")
    best = {}
    for key in sorted(acc):
        a = acc[key]; n = len(a["D1"])
        vals = {k: 100 * mean(x) for k, x in a.items() if x}
        tw = {k: vals.get(k, 0) for k in ("D2", "D3", "D4")}
        best[key] = max(tw, key=tw.get)
        print(f"  {key[0] + '·' + key[1]:>18}{n:>7}{vals.get('D1', float('nan')):>11.1f}%{vals.get('D2', float('nan')):>15.1f}%"
              f"{vals.get('D3', float('nan')):>15.1f}%{vals.get('D4', float('nan')):>12.1f}%   → mejor TWAP: {best[key]}")

    # ---------------- PARTE 2 ----------------
    R = {}   # (v, reg, x) -> filas
    for slug, w in W.items():
        if slug not in base: continue
        v = w["v"]; wlen = 300 if v == "5m" else 900; ws = w["ws"]; close = ws + wlen
        reg, L = regime(v, ws)
        if reg == "puntual": continue
        s0, tini, wsgn = base[slug]
        bd = best.get((v, reg), "D3")
        ref = s0 if bd in ("D2", "D4") else tini
        if ref is None: continue
        for x in XS[v]:
            t = close - x
            cur = le(t, 12)
            if cur is None: continue
            if bd == "D4":
                kn = avg(ws, t)
                if kn is None: continue
                exp = (kn * (wlen - x) + cur * x) / wlen
            elif x >= L:
                exp = cur
            else:
                kn = avg(close - L, t)
                exp = cur if kn is None else (kn * (L - x) + cur * x) / L
            m = exp - ref
            if sgn(m) == 0 or sgn(cur - s0) == 0: continue
            tw = "Up" if m > 0 else "Down"; sp = "Up" if cur > s0 else "Down"

            def fresh(X):
                tss, asks = w[X]; k = bisect.bisect_left(tss, t)
                if k < len(tss) and tss[k] <= t + 3 and tss[k] < close and 0 < asks[k] < 1: return asks[k]
                return None
            R.setdefault((v, reg, x), []).append({"ws": ws, "m": abs(m), "tw": tw, "sp": sp,
                                                 "atw": fresh(tw), "asp": fresh(sp), "w": wsgn})

    def stats(rows, side_key, ask_key, mid):
        rr = [r for r in rows if r[ask_key] is not None]
        if len(rr) < 20: return None
        def ev(s): return 100 * mean([(1 if (r[side_key] == "Up") == (r["w"] > 0) else 0) - r[ask_key] - fee(r[ask_key]) for r in s])
        tr = [r for r in rr if r["ws"] < mid]; te = [r for r in rr if r["ws"] >= mid]
        byw = {}
        for r in rr: byw.setdefault(week(r["ws"]), []).append(r)
        wp = sum(1 for s in byw.values() if len(s) >= 10 and ev(s) > 0); wt = sum(1 for s in byw.values() if len(s) >= 10)
        gana = 100 * mean([1 if (r[side_key] == "Up") == (r["w"] > 0) else 0 for r in rr])
        return (len(rr), mean([r[ask_key] for r in rr]), gana, ev(rr), ev(tr) if tr else float("nan"),
                ev(te) if te else float("nan"), f"{wp}/{wt}")

    print("\n" + "=" * 96)
    print("  PARTE 2 — COMPRAR EL LADO QUE MARCA EL TWAP PARCIAL (ask fresco, aguantar, neto fee) · régimen TWAP")
    print("=" * 96)
    print(f"  {'mercado·rég·x':>18}{'caso':>22}{'n':>6}{'ask':>7}{'gana%':>7}{'EV':>8}{'tr':>7}{'te':>7}{'sem+':>7}")
    for key in sorted(R):
        rows = R[key]
        if len(rows) < 50: continue
        mid = sorted(r["ws"] for r in rows)[len(rows) // 2]
        lab = f"{key[0]}·{key[1]}·T-{key[2]}"
        cases = [("TWAP-líder (todas)", rows, "tw", "atw"), ("spot-líder (todas)", rows, "sp", "asp")]
        dis = [r for r in rows if r["tw"] != r["sp"]]
        cases += [("DISCREPAN: TWAP-líder", dis, "tw", "atw"), ("DISCREPAN: spot-líder", dis, "sp", "asp")]
        for lo_name, lo, hi in MB:
            cases.append((f"TWAP-líder margen {lo_name}", [r for r in rows if lo <= r["m"] < hi], "tw", "atw"))
        for name, sub, sk, ak in cases:
            s = stats(sub, sk, ak, mid)
            if s is None: print(f"  {lab:>18}{name:>22}{'(pocos)':>8}"); continue
            n, ask, g, ev, tr, te, wk = s
            print(f"  {lab:>18}{name:>22}{n:>6}{ask:>7.3f}{g:>6.0f}%{ev:>+8.2f}{tr:>+7.2f}{te:>+7.2f}{wk:>7}")
        print()
    print("LECTURA: Parte 1 dice la regla real. Parte 2: si 'DISCREPAN: TWAP-líder' tiene EV + en tr y te y muchas")
    print("semanas+, el mercado se guía por el precio actual mientras resuelve el TWAP → EDGE calculable desde la Pi")
    print("(solo hace falta el spot en tiempo real y la media de los últimos 30/60 s). El desglose por margen dice cuándo.")


if __name__ == "__main__":
    try: sys.stdout.reconfigure(encoding="utf-8")
    except Exception: pass
    main()
