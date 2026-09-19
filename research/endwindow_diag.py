"""
endwindow_diag.py — FORENSE del resultado de endwindow_lag. ¿El ask bajo (comprar a 0,50 algo que gana el
86%) es REAL o ARTEFACTO? Para las ventanas del caso caliente (5m, |mov|≥THRESH a Δ=20s) saca, en el snapshot
de DECISIÓN, el libro de AMBOS lados: ask/bid/last del ganador-spot y del perdedor, y su SUMA. Split train/test.

 - ask_ganador + ask_perdedor ≈ 1  Y  ask_ganador ≈ último_trade  → libro coherente → el mercado está de
   verdad a ~0,50 y resuelve decisivo → edge REAL (aunque reciente).
 - ask_ganador ≪ último_trade  o  suma lejos de 1  → ask rancio/fantasma → ARTEFACTO (y problema del lab).

    cd ~/polymarket-btc-up-down/research && python3 endwindow_diag.py
"""
import csv, os, sys, glob, json, time, bisect, urllib.request

DIR = os.path.join(os.path.dirname(__file__), "lab")
DELTA = 20
MOVE = 50
TOL_SPOT = 6
CACHE = os.path.join(DIR, "clob_reso_mmtoxic.csv")


def get(url, tries=2):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "diag/1.0"})
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


def load_spot():
    out = []
    for path in sorted(glob.glob(os.path.join(DIR, "spot_*.csv"))):
        with open(path, encoding="utf-8") as fh:
            rd = csv.reader(fh); next(rd, None)
            for row in rd:
                try: out.append((int(row[0]), float(row[1])))
                except Exception: continue
    out.sort()
    return out, [t for t, _ in out]


def near_le(series, idx, t, tol):
    if not series: return None
    i = bisect.bisect_right(idx, t) - 1
    if i >= 0 and t - series[i][0] <= tol: return series[i]
    return None


def load_books_full():
    """slug -> {cid, ws, wlen, v, sides:{Up:[(ts,bid,ask,last)], Down:[...]}}"""
    W = {}
    for path in sorted(glob.glob(os.path.join(DIR, "books_*.csv"))):
        with open(path, encoding="utf-8") as fh:
            rd = csv.reader(fh); next(rd, None)
            for row in rd:
                if len(row) < 11: continue
                slug = row[1]
                v = "5m" if slug.startswith("btc-updown-5m-") else ("15m" if slug.startswith("btc-updown-15m-") else None)
                if v is None or row[3] not in ("Up", "Down"): continue
                try:
                    ts = int(row[0])
                    bid = float(row[4]) if row[4] else None
                    ask = float(row[10]) if row[10] else None
                    last = float(row[16]) if len(row) > 16 and row[16] else None
                except Exception: continue
                if ask is None: continue
                wl = 300 if v == "5m" else 900
                w = W.setdefault(slug, {"cid": row[2], "ws": int(slug.split("-")[-1]), "wlen": wl, "v": v,
                                        "sides": {"Up": [], "Down": []}})
                w["sides"][row[3]].append((ts, bid, ask, last))
    return W


def first_ge(rows, t0, tmax):
    for rec in rows:
        if rec[0] >= t0:
            return rec if rec[0] <= tmax else None
    return None


def main():
    spot, sidx = load_spot()
    W = load_books_full()
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
        if w: reso[cid] = w
        return w

    recs = []   # (ws, wask, wbid, wlast, lask, sumask, dir, winner, hit)
    for slug, w in W.items():
        if w["v"] != "5m": continue
        ws = w["ws"]; wlen = w["wlen"]
        for s in ("Up", "Down"): w["sides"][s].sort()
        t = ws + wlen - DELTA
        sp = near_le(spot, sidx, t, TOL_SPOT)
        op = near_le(spot, sidx, ws, TOL_SPOT)
        if sp is None or op is None: continue
        spot_ts, end_px = sp
        move = end_px - op[1]
        if abs(move) < MOVE: continue
        sdir = "Up" if move > 0 else "Down"; ldir = "Down" if sdir == "Up" else "Up"
        wq = first_ge(w["sides"][sdir], spot_ts, ws + wlen)
        lq = first_ge(w["sides"][ldir], spot_ts, ws + wlen)
        if wq is None or lq is None: continue
        win = resolve(w["cid"])
        if win not in ("Up", "Down"): continue
        _, wbid, wask, wlast = wq; _, lbid, lask, llast = lq
        recs.append((ws, wask, wbid, wlast, lask, (wask + lask) if lask is not None else None,
                     sdir, win, 1 if win == sdir else 0))

    n = len(recs)
    print(f"ventanas 5m con |mov|≥${MOVE} a Δ={DELTA}s: {n}")
    if not n: return

    def mean(xs):
        xs = [x for x in xs if x is not None]; return sum(xs) / len(xs) if xs else float("nan")
    mid = sorted(r[0] for r in recs)[n // 2]
    for lbl, sub in (("TRAIN (mitad antigua)", [r for r in recs if r[0] < mid]),
                     ("TEST  (mitad reciente)", [r for r in recs if r[0] >= mid])):
        m = len(sub)
        if not m: continue
        acc = 100 * sum(r[8] for r in sub) / m
        print(f"\n── {lbl}  n={m}  acc={acc:.0f}%")
        print(f"   ask_ganador medio : {mean([r[1] for r in sub]):.3f}")
        print(f"   bid_ganador medio : {mean([r[2] for r in sub]):.3f}")
        print(f"   last_ganador medio: {mean([r[3] for r in sub]):.3f}   (si ≫ ask → ask rancio/fantasma)")
        print(f"   ask_perdedor medio: {mean([r[4] for r in sub]):.3f}")
        print(f"   SUMA asks (gan+perd): {mean([r[5] for r in sub]):.3f}   (coherente ≈ 1.00)")

    print("\nEjemplos (los 12 con ask_ganador más bajo del TEST):")
    ex = sorted([r for r in recs if r[0] >= mid], key=lambda r: r[1])[:12]
    print(f"   {'fecha(UTC)':>17}{'dir':>5}{'gan_ask':>8}{'gan_bid':>8}{'gan_last':>9}{'perd_ask':>9}{'suma':>7}{'ganó':>6}")
    for r in ex:
        d = time.strftime("%m-%d %H:%M", time.gmtime(r[0]))
        la = f"{r[4]:.3f}" if r[4] is not None else "  -"
        ll = f"{r[3]:.3f}" if r[3] is not None else "  -"
        su = f"{r[5]:.3f}" if r[5] is not None else "  -"
        print(f"   {d:>17}{r[6]:>5}{r[1]:>8.3f}{(r[2] if r[2] is not None else 0):>8.3f}{ll:>9}{la:>9}{su:>7}{('sí' if r[8] else 'no'):>6}")
    print("\nLECTURA: si en TEST 'suma asks'≈1 y 'ask_ganador'≈'last' → libro coherente, mercado a ~0,50 que")
    print("resuelve decisivo = edge real reciente. Si 'last'≫'ask' o 'suma' lejos de 1 → ask fantasma = artefacto.")


if __name__ == "__main__":
    try: sys.stdout.reconfigure(encoding="utf-8")
    except Exception: pass
    main()
