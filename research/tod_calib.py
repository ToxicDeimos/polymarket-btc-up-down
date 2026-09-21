"""
tod_calib.py — ¿El mercado 5m es más floja/mispriceado en ciertas HORAS/DÍAS (baja liquidez)? Los tests del
universo promedian sobre todas las horas y podrían ocultar una franja +EV. Aquí: por ventana, a ws+150s, el
favorito (ask más alto) y si gana; EV = gana − ask − fee. Agregado por HORA (UTC) y por DÍA de semana, con
train/test. Si alguna franja es +EV y estable → edge observable por reloj (sin necesitar a los ganadores).

    cd ~/polymarket-btc-up-down/research && python3 tod_calib.py
"""
import csv, os, sys, glob, json, time, bisect, urllib.request

DIR = os.path.join(os.path.dirname(__file__), "lab")
TOL = 12; TDEC = 150
CACHE = os.path.join(DIR, "clob_reso_mmtoxic.csv")


def get(url, tries=2):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "tod/1.0"})
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
    tmp = {}
    for path in sorted(glob.glob(os.path.join(DIR, "books_*.csv"))):
        with open(path, encoding="utf-8") as fh:
            rd = csv.reader(fh); next(rd, None)
            for row in rd:
                if len(row) < 11 or not row[1].startswith("btc-updown-5m-") or row[3] not in ("Up", "Down"):
                    continue
                try:
                    ts = int(row[0]); ask = float(row[10]) if row[10] else None
                except Exception: continue
                if ask is None: continue
                w = tmp.setdefault(row[1], {"cid": row[2], "ws": int(row[1].split("-")[-1]), "Up": [], "Down": []})
                w[row[3]].append((ts, ask))
    for w in tmp.values():
        for s in ("Up", "Down"): w[s].sort()
    return tmp


def le(rows, t, tol=TOL):
    if not rows: return None
    ts = [r[0] for r in rows]; i = bisect.bisect_right(ts, t) - 1
    if i >= 0 and t - rows[i][0] <= tol: return rows[i][1]
    return None


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

    recs = []; done = 0
    for slug, w in W.items():
        win = resolve(w["cid"]); done += 1
        if done % 3000 == 0: print(f"   … {done}/{len(W)}")
        if win not in ("Up", "Down"): continue
        ws = w["ws"]; t = ws + TDEC
        au = le(w["Up"], t); ad = le(w["Down"], t)
        if au is None or ad is None: continue
        fav = "Up" if au >= ad else "Down"; fa = max(au, ad)
        if not (0.0 < fa < 1.0): continue
        g = time.gmtime(ws)
        recs.append((ws, fa, 1 if win == fav else 0, g.tm_hour, g.tm_wday))
    n = len(recs)
    print(f"ventanas: {n}")
    if not n: return
    def mean(xs): return sum(xs) / len(xs) if xs else float("nan")
    mid = sorted(r[0] for r in recs)[n // 2]
    base = 100 * mean([r[2] - r[1] - fee(r[1]) for r in recs])
    print(f"BASELINE (comprar favorito, todas las horas): EV {base:+.2f}pp\n")

    def tab(name, keyidx, labels):
        print("=" * 66 + f"\n  EV DEL FAVORITO por {name} (UTC)\n" + "=" * 66)
        print(f"  {name:>10}{'n':>7}{'ask':>7}{'gana%':>7}{'EV':>8}{'EV_tr':>8}{'EV_te':>8}")
        for k, lab in labels:
            sub = [r for r in recs if r[keyidx] == k]
            m = len(sub)
            if m < 50: continue
            ask = mean([r[1] for r in sub]); wr = 100 * mean([r[2] for r in sub])
            ev = 100 * mean([r[2] - r[1] - fee(r[1]) for r in sub])
            tr = [r for r in sub if r[0] < mid]; te = [r for r in sub if r[0] >= mid]
            evtr = 100 * mean([r[2] - r[1] - fee(r[1]) for r in tr]) if tr else float("nan")
            evte = 100 * mean([r[2] - r[1] - fee(r[1]) for r in te]) if te else float("nan")
            print(f"  {lab:>10}{m:>7}{ask:>7.3f}{wr:>6.0f}%{ev:>+7.2f}{evtr:>+8.2f}{evte:>+8.2f}")

    tab("hora", 3, [(h, f"{h:02d}h") for h in range(24)])
    tab("día", 4, list(zip(range(7), ["lun", "mar", "mié", "jue", "vie", "sáb", "dom"])))
    print("\nLECTURA: alguna hora/día con EV claramente + y estable (tr y te) → franja mispriceada explotable por")
    print("reloj. Si todo ≈ baseline (−) → no hay efecto horario; el mercado es igual de eficiente a toda hora.")


if __name__ == "__main__":
    try: sys.stdout.reconfigure(encoding="utf-8")
    except Exception: pass
    main()
