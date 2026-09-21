"""
overshoot_bt.py — RE-VALIDA el edge de fadear overshoots con una definición POR PRECIO (transfer-safe), no
por flujo. El debug del WSS mostró que en vivo casi todo es BUY y los SELL son diminutos → un "dump de X"
llega como BUY grande de ¬X, no como SELL de X. La definición de flujo (size/side) NO transfiere del data-api
al WSS. La de PRECIO sí: el libro del WSS == books_*.csv. Overshoot = el best_bid de X CAYÓ ≥DROP¢ respecto a
su máximo en los últimos LOOKBACK s. Al detectarlo: comprar X al ask (+REACT), aguantar a resolución.

Si es +EV y estable (train/test) a algún DROP → el detector por caída-de-bid del bot está VALIDADO y transfiere
en vivo. Barre DROP. Solo 5m (donde el edge existía). Neto de fee taker. 1 trigger por ventana/lado (cooldown).

    cd ~/polymarket-btc-up-down/research && python3 overshoot_bt.py
"""
import csv, os, sys, glob, json, time, bisect, urllib.request

DIR = os.path.join(os.path.dirname(__file__), "lab")
WLEN = 300; REACT = 5; BUFFER = 5; TOL = 12; LOOKBACK = 20
DROPS = (0.02, 0.03, 0.04, 0.05, 0.06)
CACHE = os.path.join(DIR, "clob_reso_mmtoxic.csv")


def get(url, tries=2):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "os/1.0"})
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


def load_books():
    W = {}
    for path in sorted(glob.glob(os.path.join(DIR, "books_*.csv"))):
        with open(path, encoding="utf-8") as fh:
            rd = csv.reader(fh); next(rd, None)
            for row in rd:
                if len(row) < 11 or not row[1].startswith("btc-updown-5m-") or row[3] not in ("Up", "Down"):
                    continue
                try:
                    ts = int(row[0])
                    bid = float(row[4]) if row[4] else None
                    ask = float(row[10]) if row[10] else None
                except Exception: continue
                if bid is None or ask is None: continue
                w = W.setdefault(row[1], {"cid": row[2], "ws": int(row[1].split("-")[-1]),
                                         "sides": {"Up": [], "Down": []}})
                w["sides"][row[3]].append((ts, bid, ask))
    for w in W.values():
        for s in ("Up", "Down"): w["sides"][s].sort()
    return W


def ask_at(rows, t, tol=TOL):
    best = None; bd = tol + 1
    for ts, _, a in rows:
        d = abs(ts - t)
        if d < bd: bd = d; best = a
        if ts > t + tol: break
    return best if bd <= tol else None


def find_triggers(rows, ws, drop):
    """primer overshoot por lado: bid ≤ (máx bid en LOOKBACK) − drop. Devuelve ts del trigger o None."""
    from collections import deque
    win = deque()
    for ts, bid, ask in rows:
        while win and win[0][0] < ts - LOOKBACK: win.popleft()
        hi = max([b for _, b in win], default=bid)
        win.append((ts, bid))
        if bid <= hi - drop and ts <= ws + WLEN - BUFFER - REACT:
            return ts
    return None


def main():
    W = load_books()
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

    data = {d: [] for d in DROPS}   # drop -> (ws, ask, hit)
    done = 0
    for slug, w in W.items():
        win = resolve(w["cid"]); done += 1
        if done % 3000 == 0: print(f"   … {done}/{len(W)}")
        if win not in ("Up", "Down"): continue
        ws = w["ws"]
        for X in ("Up", "Down"):
            rows = w["sides"][X]
            if len(rows) < 5: continue
            for drop in DROPS:
                tg = find_triggers(rows, ws, drop)
                if tg is None: continue
                oa = ask_at(rows, tg + REACT)
                if oa is None or not (0.0 < oa < 1.0): continue
                data[drop].append((ws, oa, 1 if win == X else 0))

    def mean(xs): return sum(xs) / len(xs) if xs else float("nan")
    print("\n" + "=" * 82)
    print("  OVERSHOOT POR PRECIO (bid cae ≥DROP en 20s) → comprar X al ask, aguantar  ·  transfer-safe (WSS)")
    print("=" * 82)
    print(f"  {'DROP':>6}{'n':>8}{'ask':>8}{'gana%':>8}{'EV':>9}{'EV_tr':>9}{'EV_te':>9}")
    for drop in DROPS:
        rows = data[drop]; n = len(rows)
        if n < 30:
            print(f"  {int(drop*100):>5}¢{n:>8}   (pocos)"); continue
        mid = sorted(r[0] for r in rows)[n // 2]
        ask = mean([r[1] for r in rows]); wr = 100 * mean([r[2] for r in rows])
        ev = 100 * mean([r[2] - r[1] - fee(r[1]) for r in rows])
        tr = [r for r in rows if r[0] < mid]; te = [r for r in rows if r[0] >= mid]
        evtr = 100 * mean([r[2] - r[1] - fee(r[1]) for r in tr]) if tr else float("nan")
        evte = 100 * mean([r[2] - r[1] - fee(r[1]) for r in te]) if te else float("nan")
        print(f"  {int(drop*100):>5}¢{n:>8}{ask:>8.3f}{wr:>7.0f}%{ev:>+8.2f}{evtr:>+8.2f}{evte:>+8.2f}")
    print("\nLECTURA: si algún DROP da EV + y ESTABLE (tr y te) → el edge de overshoot es REAL con definición de")
    print("PRECIO → el detector por caída-de-bid del bot transfiere en vivo (independiente de size/side). Ese DROP")
    print("es el que hay que poner en dump_quoter (DROP_THR). Si todo ~0/− → no transfiere y hay que repensarlo.")


if __name__ == "__main__":
    try: sys.stdout.reconfigure(encoding="utf-8")
    except Exception: pass
    main()
