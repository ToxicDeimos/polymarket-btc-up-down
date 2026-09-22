"""
maker_depth.py — ¿A qué PROFUNDIDAD poner el bid de maker? Ahora que el taker está cerrado (mercado calibrado),
el único edge posible es el fill de maker: comprar por debajo del ask. Este optimiza la profundidad D del bid
en reposo sobre el FAVORITO, con la CINTA REAL: postea bid = best_bid(t0) − D; se llena cuando un SELL agresor
del favorito CRUZA ese precio (fill al límite, orden de $1 = se llena entero); aguanta a resolución + rebate.

Por cada D: %fill · precio medio de entrada · %acierto de los fills · EV/ventana (con no-fills=0) · EV/fill ·
train/test · EV(−top1%) (gate de amplitud: el óptimo debe ser BROAD, no de pelotazos). El D óptimo = máx
EV/ventana que además sea robusto (tr,te + y trim +). Ese es el punto para las órdenes reales de $1.

CAVEAT: el fill asume ganar la cola si un SELL cruza (cota superior); deeper = menos competencia de cola → más
realista. La cifra REAL de fill solo la darán órdenes de $1 de verdad; esto elige DÓNDE ponerlas.

    cd ~/polymarket-btc-up-down/research && python3 maker_depth.py
"""
import csv, os, sys, glob, json, time, bisect, urllib.request

DIR = os.path.join(os.path.dirname(__file__), "lab")
TOL = 12; T0 = 120; WLEN = 300; LO, HI = 0.60, 0.85
DEPTHS = (0.0, 0.01, 0.02, 0.03, 0.04, 0.05, 0.07)
REBATE = 0.20 * 0.07
CACHE = os.path.join(DIR, "clob_reso_mmtoxic.csv")


def get(url, tries=2):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "mkd/1.0"})
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
    """slug -> {cid, ws, Up:[(ts,bid,ask)], Down:[...]}"""
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


def load_sells():
    """cid -> lista (ts, outcome, price) de SELLs agresores (dedup)"""
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


def le(rows, t, tol=TOL):
    ts = [r[0] for r in rows]; i = bisect.bisect_right(ts, t) - 1
    return rows[i] if (i >= 0 and t - rows[i][0] <= tol) else None


def main():
    W = load_books(); SELLS = load_sells()
    print(f"ventanas 5m: {len(W)} · cids con SELLs: {len(SELLS)}")
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

    # por ventana: favorito, bid0, won, SELLs del favorito tras T0
    jobs = []; done = 0
    for slug, w in W.items():
        ws = w["ws"]; t0 = ws + T0
        bu = le(w["Up"], t0); bd = le(w["Down"], t0)
        done += 1
        if done % 3000 == 0: print(f"   … {done}/{len(W)}")
        if bu is None or bd is None: continue
        fav = "Up" if bu[2] >= bd[2] else "Down"; fav_ask = max(bu[2], bd[2]); fav_bid = (bu if fav == "Up" else bd)[1]
        if not (LO <= fav_ask <= HI): continue
        win = resolve(w["cid"])
        if win not in ("Up", "Down"): continue
        sells = [(ts, pr) for ts, oc, pr in SELLS.get(w["cid"], []) if oc == fav and t0 < ts <= ws + WLEN]
        jobs.append((ws, fav_bid, 1 if win == fav else 0, sells))
    N = len(jobs)
    print(f"ventanas favorito en zona [{LO},{HI}] con resolución: {N}")
    if not N: return

    def mean(xs): return sum(xs) / len(xs) if xs else float("nan")
    mid = sorted(j[0] for j in jobs)[N // 2]
    print("\n" + "=" * 92)
    print("  PROFUNDIDAD ÓPTIMA DEL BID DE MAKER (favorito 5m, cinta real, aguantar a resolución + rebate)")
    print("=" * 92)
    print(f"  {'D(¢)':>5}{'%fill':>7}{'precio':>8}{'acierto':>9}{'EV/vent':>9}{'EV/fill':>9}{'tr':>7}{'te':>7}{'−top1%':>8}")
    for D in DEPTHS:
        pnls = []; fills = []
        for ws_, bid0, won, sells in jobs:
            bp = round(bid0 - D, 2)
            hit_fill = any(pr <= bp for _, pr in sells) and 0.0 < bp < 1.0
            if hit_fill:
                pnl = (won - bp + reb(bp)) * 100
                pnls.append((ws_, pnl)); fills.append(won)
            else:
                pnls.append((ws_, 0.0))
        nf = len(fills)
        if nf < 30:
            print(f"  {int(D*100):>4}¢{100*nf/N:>6.0f}%   (pocos fills)"); continue
        # precio y acierto de los fills
        fillbids = [round(b - D, 2) for _, b, _, s in jobs if any(pr <= round(b - D, 2) for _, pr in s) and 0 < round(b - D, 2) < 1]
        evvent = mean([p for _, p in pnls])
        evfill = mean([p for _, p in pnls if p != 0.0]) if any(p != 0 for _, p in pnls) else 0.0
        wr = 100 * mean(fills)
        tr = [p for ws_, p in pnls if ws_ < mid]; te = [p for ws_, p in pnls if ws_ >= mid]
        vals = sorted([p for _, p in pnls], reverse=True); k = max(1, int(0.01 * len(vals)))
        trim = mean(vals[k:])
        print(f"  {int(D*100):>4}¢{100*nf/N:>6.0f}%{mean(fillbids):>8.3f}{wr:>8.0f}%{evvent:>+8.2f}{evfill:>+9.2f}"
              f"{mean(tr):>+7.2f}{mean(te):>+7.2f}{trim:>+8.2f}")
    print("\nLECTURA: el D óptimo = máx 'EV/vent' que además sea ROBUSTO (tr y te + Y '−top1%' + = no pelotazos).")
    print("'%fill' cae con la profundidad; 'precio' baja (entras más barato); 'acierto' dice si esos dumps")
    print("revierten o eran cuchillos. Ese D es dónde poner el límite real de $1. Todo cota superior de cola.")


if __name__ == "__main__":
    try: sys.stdout.reconfigure(encoding="utf-8")
    except Exception: pass
    main()
