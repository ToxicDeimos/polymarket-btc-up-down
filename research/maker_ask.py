"""
maker_ask.py — El lado que NUNCA probamos. Todo maker que testeamos fue postear BID (te llenan SELLs = adverso
en toda franja, maker_zone). Pero el WSS mostró que el flujo es casi todo BUYS (los SELL diminutos). O sea:
la marea del mercado son COMPRADORES, probablemente ruido (retail apostando). Venderles = postear un ASK y que
un BUY lo levante. Si esos compradores sobrepagan, VENDERLES es +EV. Simétrico exacto a maker_zone, sin probar.

Por (D, franja de precio del outcome): postea ask = ask(t0)+D en X; se llena si un BUY suyo cruza (≥ ap);
"vendo X" = pago $1 si X gana. PnL = ap − ganó_X + rebate. Aguanto a resolución. Busco franja donde 'X gana%' <
'precio venta' (les vendo por encima de la prob real = sobrepagan) con EV/fill + estable. Cinta real.

    cd ~/polymarket-btc-up-down/research && python3 maker_ask.py
"""
import csv, os, sys, glob, json, time, bisect, urllib.request

DIR = os.path.join(os.path.dirname(__file__), "lab")
TOL = 12; T0 = 120; WLEN = 300
DEPTHS = (0.0, 0.02, 0.04)
ZONES = [(0.20, 0.35), (0.35, 0.50), (0.50, 0.65), (0.65, 0.80), (0.80, 0.92)]
REBATE = 0.20 * 0.07
CACHE = os.path.join(DIR, "clob_reso_mmtoxic.csv")


def get(url, tries=2):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "mka/1.0"})
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
                    ts = int(row[0])
                    bid = float(row[4]) if row[4] else None
                    ask = float(row[10]) if row[10] else None
                except Exception: continue
                if bid is None or ask is None: continue
                w = W.setdefault(row[1], {"cid": row[2], "ws": int(row[1].split("-")[-1]), "Up": [], "Down": []})
                w[row[3]].append((ts, bid, ask))
    for w in W.values():
        for s in ("Up", "Down"): w[s].sort()
    return W


def load_buys():
    seen = set(); T = {}
    for path in sorted(glob.glob(os.path.join(DIR, "wintrades_*.csv"))):
        with open(path, encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                if r.get("trade_side") != "BUY" or r.get("outcome") not in ("Up", "Down"): continue
                if not r.get("slug", "").startswith("btc-updown-5m-"): continue
                k = (r.get("tx", ""), r["outcome"], r.get("price"))
                if k in seen: continue
                seen.add(k)
                try:
                    ts = int(float(r["ts_trade"])); pr = float(r["price"])
                except Exception: continue
                T.setdefault(r["cid"], []).append((ts, r["outcome"], pr))
    return T


def le(rows, t, tol=TOL):
    ts = [r[0] for r in rows]; i = bisect.bisect_right(ts, t) - 1
    return rows[i] if (i >= 0 and t - rows[i][0] <= tol) else None


def main():
    W = load_books(); BUYS = load_buys()
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

    obs = {(D, zi): [] for D in DEPTHS for zi in range(len(ZONES))}
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
            zi = next((i for i, (lo, hi) in enumerate(ZONES) if lo <= askx < hi), None)
            if zi is None: continue
            buys = [pr for ts, oc, pr in BUYS.get(w["cid"], []) if oc == X and t0 < ts <= close]
            wonX = 1 if win == X else 0
            for D in DEPTHS:
                ap = round(askx + D, 2)
                if not (0.0 < ap < 1.0): continue
                filled = any(pr >= ap for pr in buys)
                pnl = (ap - wonX + reb(ap)) * 100 if filled else 0.0   # vendo X a ap, pago 1 si X gana
                obs[(D, zi)].append((w["ws"], pnl, filled, wonX, ap))

    def mean(xs): return sum(xs) / len(xs) if xs else float("nan")
    print("\n" + "=" * 96)
    print("  MAKER LADO ASK — vender X a los compradores (flujo dominante) · 5m · cinta real · aguantar+rebate")
    print("=" * 96)
    for D in DEPTHS:
        print(f"\n  ── ask a +{int(D*100)}¢ sobre el ask " + "─" * 34)
        print(f"    {'franja':>11}{'N':>7}{'%fill':>7}{'precio_v':>9}{'X gana%':>9}{'EV/vent':>9}{'EV/fill':>9}{'tr':>7}{'te':>7}")
        for zi, (lo, hi) in enumerate(ZONES):
            rows = obs[(D, zi)]; N = len(rows); fills = [r for r in rows if r[2]]
            if len(fills) < 30:
                print(f"    {f'{lo:.2f}-{hi:.2f}':>11}{N:>7}{100*len(fills)/N if N else 0:>6.0f}%  (pocos)"); continue
            mid = sorted(r[0] for r in rows)[N // 2]
            xg = 100 * mean([r[3] for r in fills]); pv = mean([r[4] for r in fills])
            evv = mean([r[1] for r in rows]); evf = mean([r[1] for r in fills])
            tr = [r[1] for r in rows if r[0] < mid]; te = [r[1] for r in rows if r[0] >= mid]
            print(f"    {f'{lo:.2f}-{hi:.2f}':>11}{N:>7}{100*len(fills)/N:>6.0f}%{pv:>9.3f}{xg:>8.0f}%"
                  f"{evv:>+8.2f}{evf:>+9.2f}{mean(tr):>+7.2f}{mean(te):>+7.2f}")
    print("\nLECTURA: vendo X a 'precio_v'; si 'X gana%' < precio_v → los compradores SOBREPAGAN (ruido) y")
    print("venderles es +EV. Busco franja con EV/fill + y estable (tr,te). Si 'X gana%' ≈ precio_v en todas →")
    print("el flujo de compra también está informado/calibrado y tampoco hay edge vendiéndoles.")


if __name__ == "__main__":
    try: sys.stdout.reconfigure(encoding="utf-8")
    except Exception: pass
    main()
