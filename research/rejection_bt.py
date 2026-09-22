"""
rejection_bt.py — Prueba la hipótesis del usuario: FALSA RUPTURA / BARRIDO DE LIQUIDEZ (evento de estructura,
no indicador continuo). El spot rompe un extremo reciente de la ventana y FALLA (revierte) → el mercado puede
tardar en repreciar → fade. Distinto de todo lo probado (indicadores continuos = eficientes).

Definición (pocos params, a priori):
  máximo de ventana hecho hace <M s y el spot ya ha caído ≥δ desde él → ruptura alcista FALLIDA → comprar Down
  mínimo de ventana hecho hace <M s y el spot ya ha rebotado ≥δ         → barrido abajo revertido  → comprar Up
1er evento por ventana. Compro la reversión al ask (+5s, pre-cierre), aguanto a resolución. Neto fee, train/
test, 5m y 15m. Barre δ ($). OJO p-hacking: estructura tiene mal historial; exijo +EV estable en AMBAS mitades.

    cd ~/polymarket-btc-up-down/research && python3 rejection_bt.py
"""
import csv, os, sys, glob, json, time, bisect, urllib.request

DIR = os.path.join(os.path.dirname(__file__), "lab")
TOL = 12; REACT = 5; BUFFER = 5; M = 90
DELTAS = (15, 30, 50)   # $ de reversión desde el extremo
CACHE = os.path.join(DIR, "clob_reso_mmtoxic.csv")


def get(url, tries=2):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "rej/1.0"})
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


def load_asks():
    W = {}
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
                v = "5m" if "-5m-" in row[1] else "15m"
                w = W.setdefault(row[1], {"cid": row[2], "ws": int(row[1].split("-")[-1]), "v": v, "Up": [], "Down": []})
                w[row[3]].append((ts, ask))
    for w in W.values():
        for s in ("Up", "Down"): w[s].sort()
    return W


def load_spot():
    out = []
    for path in sorted(glob.glob(os.path.join(DIR, "spot_*.csv"))):
        with open(path, encoding="utf-8") as fh:
            rd = csv.reader(fh); next(rd, None)
            for row in rd:
                try: out.append((int(row[0]), float(row[1])))
                except Exception: continue
    out.sort(); return [t for t, _ in out], [p for _, p in out]


def le_ask(rows, t, tol=TOL):
    ts = [r[0] for r in rows]; i = bisect.bisect_right(ts, t) - 1
    return rows[i][1] if (i >= 0 and t - rows[i][0] <= tol) else None


def main():
    W = load_asks(); stss, spx = load_spot()
    print(f"ventanas: {sum(1 for w in W.values() if w['v']=='5m')} 5m / {sum(1 for w in W.values() if w['v']=='15m')} 15m · spot {len(stss)}")
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

    def detect(ws, wlen, delta):
        """1er rechazo de extremo. Devuelve (t, outcome_a_comprar) o None."""
        close = ws + wlen
        lo = bisect.bisect_left(stss, ws); hi = bisect.bisect_right(stss, close)
        himax = -1e18; himax_t = 0; lomin = 1e18; lomin_t = 0
        for i in range(lo, hi):
            t = stss[i]; s = spx[i]
            if s > himax: himax = s; himax_t = t
            if s < lomin: lomin = s; lomin_t = t
            if t < ws + 60 or t > close - BUFFER - REACT: continue
            # ruptura alcista fallida: máximo reciente, ya caído ≥δ
            if t - himax_t <= M and himax - s >= delta:
                return (t, "Down")
            # barrido abajo revertido: mínimo reciente, ya rebotado ≥δ
            if t - lomin_t <= M and s - lomin >= delta:
                return (t, "Up")
        return None

    def mean(xs): return sum(xs) / len(xs) if xs else float("nan")

    for v in ("5m", "15m"):
        wins = [w for w in W.values() if w["v"] == v]
        wlen = 300 if v == "5m" else 900
        print("\n" + "=" * 80)
        print(f"  RECHAZO DE EXTREMO (falsa ruptura / barrido) {v} · comprar la reversión, aguantar")
        print("=" * 80)
        print(f"  {'δ($)':>6}{'n':>7}{'ask':>7}{'gana%':>7}{'EV':>8}{'EV_tr':>8}{'EV_te':>8}")
        for delta in DELTAS:
            recs = []; done = 0
            for w in wins:
                done += 1
                ev = detect(w["ws"], wlen, delta)
                if ev is None: continue
                t, out = ev
                ask = le_ask(w[out], t + REACT)
                if ask is None or not (0.0 < ask < 1.0): continue
                win = resolve(w["cid"])
                if win not in ("Up", "Down"): continue
                recs.append((w["ws"], ask, 1 if win == out else 0))
            n = len(recs)
            if n < 30:
                print(f"  {delta:>6}{n:>7}   (pocos)"); continue
            mid = sorted(r[0] for r in recs)[n // 2]
            ask = mean([r[1] for r in recs]); wr = 100 * mean([r[2] for r in recs])
            e = 100 * mean([r[2] - r[1] - fee(r[1]) for r in recs])
            tr = [r for r in recs if r[0] < mid]; te = [r for r in recs if r[0] >= mid]
            etr = 100 * mean([r[2] - r[1] - fee(r[1]) for r in tr]) if tr else float("nan")
            ete = 100 * mean([r[2] - r[1] - fee(r[1]) for r in te]) if te else float("nan")
            print(f"  {delta:>6}{n:>7}{ask:>7.3f}{wr:>6.0f}%{e:>+7.2f}{etr:>+8.2f}{ete:>+8.2f}")
    print("\nLECTURA: si algún δ da EV + y estable (tr y te) → la falsa ruptura/barrido SÍ es un edge de estructura")
    print("en este mercado (el usuario tiene razón), computable y accionable por la Pi. Si todo −/inestable → el")
    print("rechazo tampoco bate al precio y confirmamos eficiencia también en price-action de estructura.")


if __name__ == "__main__":
    try: sys.stdout.reconfigure(encoding="utf-8")
    except Exception: pass
    main()
