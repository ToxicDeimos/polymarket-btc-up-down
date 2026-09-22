"""
calibration.py — TEST DEFINITIVO de eficiencia al taker: ¿el ACIERTO real = el PRECIO, en toda la escala? Si
sí (curva = diagonal), NINGUNA señal de taker puede ganar (matemáticamente), y dejamos de probar hipótesis una
a una. Si alguna franja de precio se DESVÍA de forma estable (acierto ≠ precio), ahí hay un edge estructural
directo (p.ej. longshots sobrevalorados / favoritos infravalorados) sin necesitar señal.

Sobre TODO el libro (5m y 15m), a varios instantes, por cada outcome registro (ask, ganó). Bucket por precio;
acierto real vs precio; EV = acierto − precio − fee; desvío = acierto − precio; train/test.

    cd ~/polymarket-btc-up-down/research && python3 calibration.py
"""
import csv, os, sys, glob, json, time, bisect, urllib.request

DIR = os.path.join(os.path.dirname(__file__), "lab")
TOL = 12; FRACS = (0.4, 0.6, 0.8)
CACHE = os.path.join(DIR, "clob_reso_mmtoxic.csv")


def get(url, tries=2):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "cal/1.0"})
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


def le(rows, t, tol=TOL):
    ts = [r[0] for r in rows]; i = bisect.bisect_right(ts, t) - 1
    return rows[i][1] if (i >= 0 and t - rows[i][0] <= tol) else None


def main():
    W = load_asks()
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

    def mean(xs): return sum(xs) / len(xs) if xs else float("nan")
    BINS = [(round(x / 100, 2), round((x + 10) / 100, 2)) for x in range(5, 95, 10)]  # 0.05-0.15 ... 0.85-0.95

    for v in ("5m", "15m"):
        wins = [w for w in W.values() if w["v"] == v]; wlen = 300 if v == "5m" else 900
        recs = []; done = 0
        for w in wins:
            win = resolve(w["cid"]); done += 1
            if done % 4000 == 0: print(f"   … {v} {done}/{len(wins)}")
            if win not in ("Up", "Down"): continue
            for fr in FRACS:
                t = w["ws"] + int(fr * wlen)
                for X in ("Up", "Down"):
                    a = le(w[X], t)
                    if a is None or not (0.0 < a < 1.0): continue
                    recs.append((w["ws"], a, 1 if win == X else 0))
        n = len(recs)
        print("\n" + "=" * 84)
        print(f"  CALIBRACIÓN {v} — comprar al ask por franja de precio · n={n} · (sano/eficiente: acierto≈precio)")
        print("=" * 84)
        print(f"  {'franja':>11}{'n':>8}{'precio':>8}{'acierto':>9}{'desvío':>8}{'EV':>8}{'EV_tr':>8}{'EV_te':>8}")
        if not n: continue
        mid = sorted(r[0] for r in recs)[n // 2]
        for lo, hi in BINS:
            sub = [r for r in recs if lo <= r[1] < hi]
            m = len(sub)
            if m < 50: continue
            pr = mean([r[1] for r in sub]); ac = mean([r[2] for r in sub])
            ev = 100 * mean([r[2] - r[1] - fee(r[1]) for r in sub])
            tr = [r for r in sub if r[0] < mid]; te = [r for r in sub if r[0] >= mid]
            evtr = 100 * mean([r[2] - r[1] - fee(r[1]) for r in tr]) if tr else float("nan")
            evte = 100 * mean([r[2] - r[1] - fee(r[1]) for r in te]) if te else float("nan")
            print(f"  {f'{lo:.2f}-{hi:.2f}':>11}{m:>8}{pr:>8.3f}{100*ac:>8.0f}%{100*(ac-pr):>+7.0f}"
                  f"{ev:>+8.2f}{evtr:>+8.2f}{evte:>+8.2f}")
    print("\nVEREDICTO: si 'desvío'≈0 y 'EV'<0 en TODA franja → mercado calibrado = eficiente al taker, ninguna")
    print("señal puede ganar, se acabó buscar. Si alguna franja tiene 'desvío' + grande y 'EV' + estable (tr y")
    print("te) → edge estructural directo por precio (comprar esa franja), sin necesidad de más hipótesis.")


if __name__ == "__main__":
    try: sys.stdout.reconfigure(encoding="utf-8")
    except Exception: pass
    main()
