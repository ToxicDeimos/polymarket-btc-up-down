"""
edge15.py — BÚSQUEDA DE EDGE DEDICADA AL MERCADO 15m. El 15m es más lento (la Pi SÍ llega) y menos líquido
que el 5m → puede tener sesgos que el 5m no. Test de CALIBRACIÓN (favorito-longshot): a un instante fijo,
agrupo por el ask del FAVORITO y mido cuánto gana de verdad. Si alguna banda gana MÁS que su ask+fee de forma
ESTABLE (train y test) → el mercado la infravalora = edge estructural (comprar esa banda y aguantar). Dos
instantes: ws+600 (5 min antes del cierre) y ws+780 (2 min, más decisivo, con runway para la Pi).

    cd ~/polymarket-btc-up-down/research && python3 edge15.py
"""
import csv, os, sys, glob, json, time, urllib.request

DIR = os.path.join(os.path.dirname(__file__), "lab")
TS = (600, 780)              # s desde el open del 15m
TOL = 10
CACHE = os.path.join(DIR, "clob_reso_mmtoxic.csv")


def get(url, tries=2):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "e15/1.0"})
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


def load15():
    W = {}
    for path in sorted(glob.glob(os.path.join(DIR, "books_*.csv"))):
        with open(path, encoding="utf-8") as fh:
            rd = csv.reader(fh); next(rd, None)
            for row in rd:
                if len(row) < 11 or not row[1].startswith("btc-updown-15m-") or row[3] not in ("Up", "Down"):
                    continue
                try:
                    ts = int(row[0]); ask = float(row[10]) if row[10] else None
                except Exception: continue
                if ask is None: continue
                w = W.setdefault(row[1], {"cid": row[2], "ws": int(row[1].split("-")[-1]),
                                         "asks": {"Up": [], "Down": []}})
                w["asks"][row[3]].append((ts, ask))
    for w in W.values():
        for s in ("Up", "Down"): w["asks"][s].sort()
    return W


def ask_le(rows, t, tol=TOL):
    best = None; bt = None
    for ts, a in rows:
        if ts <= t: best = a; bt = ts
        else: break
    return best if (bt is not None and t - bt <= tol) else None


def main():
    W = load15()
    print(f"ventanas 15m: {len(W)}")
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

    # recolectar por instante: (ws, fav_ask, hit)
    data = {t: [] for t in TS}
    print(f"resolviendo {len(W)} ventanas 15m (cacheado)…"); done = 0
    for slug, w in W.items():
        win = resolve(w["cid"]); done += 1
        if done % 1500 == 0: print(f"   … {done}/{len(W)}")
        if win not in ("Up", "Down"): continue
        ws = w["ws"]
        for t in TS:
            au = ask_le(w["asks"]["Up"], ws + t); ad = ask_le(w["asks"]["Down"], ws + t)
            if au is None or ad is None: continue
            fav = "Up" if au >= ad else "Down"; fa = max(au, ad)
            data[t].append((ws, fa, 1 if win == fav else 0))

    def mean(xs): return sum(xs) / len(xs) if xs else float("nan")
    BINS = [(0.50, 0.60), (0.60, 0.70), (0.70, 0.80), (0.80, 0.90), (0.90, 1.01)]
    for t in TS:
        rows = data[t]; n = len(rows)
        if not n: continue
        mid = sorted(r[0] for r in rows)[n // 2]
        print("\n" + "=" * 88)
        print(f"  CALIBRACIÓN 15m a t=ws+{t}s ({(900-t)//60}min {(900-t)%60}s antes del cierre)  ·  n={n}")
        print("=" * 88)
        print(f"  {'banda_ask':>11}{'n':>7}{'ask_med':>9}{'gana%':>7}{'edge':>7}{'EV':>8}{'EV_tr':>8}{'EV_te':>8}")
        for lo, hi in BINS:
            sub = [r for r in rows if lo <= r[1] < hi]
            m = len(sub)
            if m < 30:
                print(f"  {f'{lo:.2f}-{hi:.2f}':>11}{m:>7}   (pocos)"); continue
            am = mean([r[1] for r in sub]); wr = mean([r[2] for r in sub])
            ev = 100 * mean([r[2] - r[1] - fee(r[1]) for r in sub])
            tr = [r for r in sub if r[0] < mid]; te = [r for r in sub if r[0] >= mid]
            evtr = 100 * mean([r[2] - r[1] - fee(r[1]) for r in tr]) if tr else float("nan")
            evte = 100 * mean([r[2] - r[1] - fee(r[1]) for r in te]) if te else float("nan")
            print(f"  {f'{lo:.2f}-{hi:.2f}':>11}{m:>7}{am:>9.3f}{100*wr:>6.0f}%{100*(wr-am):>+6.0f}"
                  f"{ev:>+8.2f}{evtr:>+8.2f}{evte:>+8.2f}")
    print("\nLECTURA: 'edge'=gana%−ask (¿el favorito de esa banda gana más de lo que cuesta?). 'EV'=gana−ask−fee.")
    print("Si alguna banda tiene EV + y ESTABLE (tr y te ambos +) → mercado 15m mal calibrado ahí = edge real")
    print("(comprar esa banda y aguantar). Si todas ~0 o −  → el 15m también es eficiente al taker.")


if __name__ == "__main__":
    try: sys.stdout.reconfigure(encoding="utf-8")
    except Exception: pass
    main()
