"""
maker_quiet.py — La palanca que la Pi SÍ puede tirar. El mapa completo (maker_zone + maker_ask) mostró que
CUALQUIER orden en reposo se llena de flujo INFORMADO (adverso), los dos lados. Los ganadores lo esquivan por
VELOCIDAD (que no tenemos). Pero hay otra vía sin velocidad: hacer mercado SOLO cuando el flujo es RUIDO. ¿Y
cuándo hay información? Cuando BTC se MUEVE. Hipótesis: la selección adversa se concentra en spot VOLÁTIL;
cuando el spot está QUIETO, quien cruza es ruido → fills favorables. Computable en la Pi (mide vol reciente,
cotiza solo si calma).

Bid en reposo (D=2¢) sobre outcomes en [0,35-0,70] (zona de los ganadores); en el fill, mido la vol del spot en
los 60s previos y segmento. Si los fills en spot QUIETO ganan ≈/> su precio (no adversos) con EV + estable →
ENCONTRAMOS el gate: hacer mercado solo en calma. Cinta real, aguantar a resolución + rebate.

    cd ~/polymarket-btc-up-down/research && python3 maker_quiet.py
"""
import csv, os, sys, glob, json, time, bisect, urllib.request

DIR = os.path.join(os.path.dirname(__file__), "lab")
TOL = 12; T0 = 120; WLEN = 300; D = 0.02; LO, HI = 0.35, 0.70
VOLB = [("<$10 (quieto)", 0, 10), ("$10-25", 10, 25), ("$25-50", 25, 50), (">$50 (turbulento)", 50, 1e9)]
REBATE = 0.20 * 0.07
CACHE = os.path.join(DIR, "clob_reso_mmtoxic.csv")


def get(url, tries=2):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "mkq/1.0"})
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


def reb(p): return REBATE * p * (1 - p)


def load_books():
    W = {}
    for path in sorted(glob.glob(os.path.join(DIR, "books_*.csv"))):
        with open(path, encoding="utf-8") as fh:
            rd = csv.reader(fh); next(rd, None)
            for row in rd:
                if len(row) < 11 or not row[1].startswith("btc-updown-5m-") or row[3] not in ("Up", "Down"):
                    continue
                try:
                    ts = int(row[0]); bid = float(row[4]) if row[4] else None; ask = float(row[10]) if row[10] else None
                except Exception: continue
                if bid is None or ask is None: continue
                w = W.setdefault(row[1], {"cid": row[2], "ws": int(row[1].split("-")[-1]), "Up": [], "Down": []})
                w[row[3]].append((ts, bid, ask))
    for w in W.values():
        for s in ("Up", "Down"): w[s].sort()
    return W


def load_sells():
    seen = set(); T = {}
    for path in sorted(glob.glob(os.path.join(DIR, "wintrades_*.csv"))):
        with open(path, encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                if r.get("trade_side") != "SELL" or r.get("outcome") not in ("Up", "Down"): continue
                if not r.get("slug", "").startswith("btc-updown-5m-"): continue
                k = (r.get("tx", ""), r["outcome"], r.get("price"))
                if k in seen: continue
                seen.add(k)
                try:
                    ts = int(float(r["ts_trade"])); pr = float(r["price"])
                except Exception: continue
                T.setdefault(r["cid"], []).append((ts, r["outcome"], pr))
    for cid in T: T[cid].sort()
    return T


def load_spot():
    out = []
    for path in sorted(glob.glob(os.path.join(DIR, "spot_*.csv"))):
        with open(path, encoding="utf-8") as fh:
            rd = csv.reader(fh); next(rd, None)
            for row in rd:
                try: out.append((int(row[0]), float(row[1])))
                except Exception: continue
    out.sort(); return [t for t, _ in out], [p for _, p in out]


def le(rows, t, tol=TOL):
    ts = [r[0] for r in rows]; i = bisect.bisect_right(ts, t) - 1
    return rows[i] if (i >= 0 and t - rows[i][0] <= tol) else None


def spot_at(stss, spx, t, tol=30):
    i = bisect.bisect_right(stss, t) - 1
    return spx[i] if (i >= 0 and t - stss[i] <= tol) else None


def main():
    W = load_books(); SELLS = load_sells(); stss, spx = load_spot()
    print(f"ventanas 5m: {len(W)}")
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

    fills = []   # (ws, bp, won, recent_vol)
    done = 0
    for slug, w in W.items():
        done += 1
        if done % 3000 == 0: print(f"   … {done}/{len(W)}")
        t0 = w["ws"] + T0; close = w["ws"] + WLEN
        win = resolve(w["cid"])
        if win not in ("Up", "Down"): continue
        for X in ("Up", "Down"):
            r0 = le(w[X], t0)
            if r0 is None: continue
            _, bidx, askx = r0
            if not (LO <= askx < HI): continue
            bp = round(bidx - D, 2)
            if not (0.0 < bp < 1.0): continue
            fill_ts = next((ts for ts, oc, pr in SELLS.get(w["cid"], []) if oc == X and pr <= bp and t0 < ts <= close), None)
            if fill_ts is None: continue
            s1 = spot_at(stss, spx, fill_ts); s0 = spot_at(stss, spx, fill_ts - 60)
            if s1 is None or s0 is None: continue
            fills.append((w["ws"], bp, 1 if win == X else 0, abs(s1 - s0)))
    n = len(fills)
    print(f"fills con vol medible: {n}")
    if not n: return

    def mean(xs): return sum(xs) / len(xs) if xs else float("nan")
    mid = sorted(f[0] for f in fills)[n // 2]
    print("\n" + "=" * 84)
    print(f"  FILL DE MAKER (bid −2¢, zona 0,35-0,70) SEGMENTADO POR VOLATILIDAD DEL SPOT (60s previos)")
    print("=" * 84)
    print(f"  {'vol spot':>18}{'n':>7}{'precio':>8}{'acierto':>9}{'EV/fill':>9}{'tr':>8}{'te':>8}")
    for lab, lo, hi in VOLB:
        sub = [f for f in fills if lo <= f[3] < hi]
        m = len(sub)
        if m < 30:
            print(f"  {lab:>18}{m:>7}   (pocos)"); continue
        pr = mean([f[1] for f in sub]); ac = 100 * mean([f[2] for f in sub])
        ev = 100 * mean([f[2] - f[1] + reb(f[1]) for f in sub])
        tr = [f for f in sub if f[0] < mid]; te = [f for f in sub if f[0] >= mid]
        evtr = 100 * mean([f[2] - f[1] + reb(f[1]) for f in tr]) if tr else float("nan")
        evte = 100 * mean([f[2] - f[1] + reb(f[1]) for f in te]) if te else float("nan")
        print(f"  {lab:>18}{m:>7}{pr:>8.3f}{ac:>8.0f}%{ev:>+8.2f}{evtr:>+8.2f}{evte:>+8.2f}")
    print("\nLECTURA: si en 'spot quieto' el 'acierto' ≈/> 'precio' y EV/fill + estable (tr,te) → la selección")
    print("adversa vive en la volatilidad, y hacer mercado SOLO en calma es el gate accionable por la Pi. Si")
    print("hasta en calma el acierto << precio → el flujo es informado siempre y no hay gate de volatilidad.")


if __name__ == "__main__":
    try: sys.stdout.reconfigure(encoding="utf-8")
    except Exception: pass
    main()
