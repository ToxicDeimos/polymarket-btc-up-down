"""
mm_toxic_backtest.py — MM con SKEW de inventario + PROTECCIÓN DE FLUJO TÓXICO, sobre la cinta REAL,
comparando 5m vs 15m. La pieza NUEVA que el simulador ingenuo no tenía y un MM real SÍ usa:
cuando entra un trade agresor GRANDE (size >= percentil), RETIRO el lado que me llenaría en adverso
durante COOLDOWN s (el flujo informado llega en oleadas → esquivo las réplicas, no la primera copia).

Pregunta 1: ¿la protección cruza el PnL (cota superior) a +EV, o el MM desde la Pi está muerto?
Pregunta 2: ¿es más eficiente en 5m o en 15m? (más round-trips, más plano, mejor PnL)

Datos del lab (mismos que usa mm_ws, así el número es COMPARABLE = cota superior, asumo ganar la cola):
  books_*.csv     → best_bid (col 4), best_ask (col 10) del favorito, cada ~5s
  wintrades_*.csv → cinta agresora por ventana: trade_side, outcome, price, size, ts_trade
Resolución por CLOB (winner), cacheada en lab/clob_reso_mmtoxic.csv para que las re-corridas sean rápidas.

    cd ~/polymarket-btc-up-down/research && python3 mm_toxic_backtest.py
    python3 mm_toxic_backtest.py 0.90 12    # opcional: percentil-tóxico y cooldown a medida
"""
import csv, os, sys, glob, json, time, urllib.request

DIR = os.path.join(os.path.dirname(__file__), "lab")
LO, HI = 0.62, 0.82          # zona del favorito (misma que mm_ws)
SKEW = 0.01                  # desplazamiento por unidad de inventario
MAXINV = 3
REBATE = 0.20 * 0.07         # rebate maker crypto
TOXIC_PCT = 0.85             # tamaño "tóxico" = este percentil de la cinta del favorito
COOLDOWN = 10                # s que retiro el lado vulnerable tras un print tóxico
CACHE = os.path.join(DIR, "clob_reso_mmtoxic.csv")


def get(url, tries=2):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "mmtox/1.0"})
            with urllib.request.urlopen(req, timeout=12) as r: return json.load(r)
        except Exception:
            if i == tries - 1: return None
            time.sleep(0.3)


def reb(p): return REBATE * p * (1 - p)


def winner_clob(cid):
    d = get(f"https://clob.polymarket.com/markets/{cid}")
    if isinstance(d, dict):
        for t in d.get("tokens", []):
            if t.get("winner") is True: return t.get("outcome")
    return None


def pctl(vals, q):
    if not vals: return None
    s = sorted(vals); return s[min(len(s) - 1, int(q * len(s)))]


def load_books():
    """slug -> {cid, ws, wlen, v, sides:{Up:[(ts,bid,ask)], Down:[...]}}"""
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
                except Exception: continue
                if bid is None and ask is None: continue
                wl = 300 if v == "5m" else 900
                w = W.setdefault(slug, {"cid": row[2], "ws": int(slug.split("-")[-1]), "wlen": wl, "v": v,
                                        "sides": {"Up": [], "Down": []}})
                w["sides"][row[3]].append((ts, bid, ask))
    return W


def load_trades():
    """cid -> [(ts, side, outcome, price, size)] (cinta agresora completa por ventana)"""
    T = {}
    for path in sorted(glob.glob(os.path.join(DIR, "wintrades_*.csv"))):
        with open(path, encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                try:
                    ts = int(float(r["ts_trade"])); price = float(r["price"]); size = float(r["size"])
                except Exception: continue
                if r.get("outcome") not in ("Up", "Down"): continue
                T.setdefault(r["cid"], []).append((ts, r.get("trade_side"), r["outcome"], price, size))
    return T


def run_mm(book, trs, fav_ask, won, toxic_size, cooldown):
    """book: sorted [(ts,bid,ask)]; trs: sorted [(ts,side,price,size)]. toxic_size=None → sin protección."""
    inv = 0; cash = 0.0; buys = sells = rt = 0; maxinv = 0
    bid_pull = ask_pull = 0
    bi = 0; bid = ask = my_bid = my_ask = None

    def requote():
        nonlocal my_bid, my_ask
        if bid is None or ask is None: return
        my_bid = round(bid - SKEW * inv, 2); my_ask = round(ask - SKEW * inv, 2)

    for ts, side, price, size in trs:
        while bi < len(book) and book[bi][0] <= ts:
            _, bid, ask = book[bi]; bi += 1
        requote()
        if my_bid is not None and my_ask is not None:
            # fill bajo el estado de retirada actual (la 1ª copia tóxica NO se puede esquivar; sí las réplicas)
            if side == "SELL" and price <= my_bid and inv < MAXINV and ts >= bid_pull:
                inv += 1; cash -= my_bid; buys += 1; maxinv = max(maxinv, inv); requote()
            elif side == "BUY" and price >= my_ask and inv > 0 and ts >= ask_pull:
                inv -= 1; cash += my_ask; sells += 1; rt += 1; requote()
        if toxic_size is not None and size >= toxic_size:   # protege los prints siguientes
            if side == "SELL": bid_pull = ts + cooldown     # venta informada → dejo de comprar
            elif side == "BUY": ask_pull = ts + cooldown    # compra informada → dejo de vender
    pnl = (cash + inv * won + (buys + sells) * reb(fav_ask)) * 100
    return dict(buys=buys, sells=sells, rt=rt, end_inv=inv, maxinv=maxinv, pnl=pnl)


def main():
    global TOXIC_PCT, COOLDOWN
    if len(sys.argv) > 1: TOXIC_PCT = float(sys.argv[1])
    if len(sys.argv) > 2: COOLDOWN = int(sys.argv[2])

    W = load_books(); T = load_trades()
    print(f"ventanas en libro: {sum(1 for w in W.values() if w['v']=='5m')} de 5m · "
          f"{sum(1 for w in W.values() if w['v']=='15m')} de 15m · cids con cinta: {len(T)}")

    # PASO 1: por ventana, fijar favorito (en zona) al 40% de la ventana; recopilar tamaños de su cinta
    jobs = []; sizes = {"5m": [], "15m": []}
    skip = {"5m": {"zona": 0, "cinta": 0}, "15m": {"zona": 0, "cinta": 0}}
    for slug, w in W.items():
        v = w["v"]; ws = w["ws"]; wlen = w["wlen"]; tdet = ws + int(0.4 * wlen)

        def ask_at(side):
            best = None
            for ts, bid, ask in w["sides"][side]:
                if ts <= tdet and ask is not None: best = ask
            return best
        aU, aD = ask_at("Up"), ask_at("Down")
        if aU is None or aD is None: skip[v]["zona"] += 1; continue
        fav = "Up" if aU >= aD else "Down"; fav_ask = max(aU, aD)
        if not (LO <= fav_ask <= HI): skip[v]["zona"] += 1; continue

        book = sorted((ts, bid, ask) for ts, bid, ask in w["sides"][fav]
                      if ws <= ts <= ws + wlen and bid is not None and ask is not None)
        trs = sorted((ts, s, p, sz) for ts, s, oc, p, sz in T.get(w["cid"], [])
                     if oc == fav and tdet <= ts <= ws + wlen)
        if len(book) < 3 or len(trs) < 3: skip[v]["cinta"] += 1; continue
        sizes[v] += [sz for _, _, _, sz in trs]
        jobs.append({"cid": w["cid"], "v": v, "fav": fav, "fav_ask": fav_ask, "book": book, "trs": trs})

    toxic = {v: pctl(sizes[v], TOXIC_PCT) for v in ("5m", "15m")}
    for v in ("5m", "15m"):
        n = len(sizes[v]); above = sum(1 for s in sizes[v] if toxic[v] is not None and s >= toxic[v])
        print(f"  {v}: umbral tóxico (p{int(TOXIC_PCT*100)}) = {toxic[v]} shares · "
              f"{above}/{n} prints tóxicos ({100*above/n:.0f}% de la cinta)" if n else f"  {v}: sin cinta")
    print(f"  saltadas — fuera de zona: 5m {skip['5m']['zona']} / 15m {skip['15m']['zona']} · "
          f"sin cinta: 5m {skip['5m']['cinta']} / 15m {skip['15m']['cinta']}")

    # cache de resolución (reutiliza todas las cachés del proyecto)
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

    # PASO 2: resolver + simular baseline (solo skew) y protegido (skew + tóxico)
    print(f"\nresolviendo {len(jobs)} ventanas por CLOB (cacheado)…")
    agg = {(v, m): {"n": 0, "rt": 0, "flat": 0, "maxinv": 0, "pnl": 0.0, "pos": 0, "buys": 0}
           for v in ("5m", "15m") for m in ("base", "prot")}
    done = 0
    for j in jobs:
        win = resolve(j["cid"])
        if win not in ("Up", "Down"): continue
        won = 1 if j["fav"] == win else 0
        for m, tsz in (("base", None), ("prot", toxic[j["v"]])):
            r = run_mm(j["book"], j["trs"], j["fav_ask"], won, tsz, COOLDOWN)
            a = agg[(j["v"], m)]
            a["n"] += 1; a["rt"] += r["rt"]; a["flat"] += (1 if r["end_inv"] == 0 else 0)
            a["maxinv"] += r["maxinv"]; a["pnl"] += r["pnl"]; a["pos"] += (1 if r["pnl"] > 0 else 0)
            a["buys"] += r["buys"]
        done += 1
        if done % 200 == 0: print(f"   … {done}/{len(jobs)}")

    # informe
    print("\n" + "=" * 78)
    print(f"  MM skew{'':2} vs skew+tóxico  ·  p{int(TOXIC_PCT*100)} / cooldown {COOLDOWN}s  ·  PnL = COTA SUPERIOR")
    print("=" * 78)
    print(f"{'':14}{'N':>6}{'RT/vent':>9}{'plano%':>8}{'máxinv':>8}{'PnL medio':>11}{'PnL>0%':>8}")
    for v in ("5m", "15m"):
        for m, lbl in (("base", f"{v} baseline"), ("prot", f"{v} protegido")):
            a = agg[(v, m)]; n = a["n"]
            if not n: print(f"{lbl:14}{'—':>6}"); continue
            print(f"{lbl:14}{n:>6}{a['rt']/n:>9.1f}{100*a['flat']/n:>7.0f}%"
                  f"{a['maxinv']/n:>8.2f}{a['pnl']/n:>+10.2f}pp{100*a['pos']/n:>7.0f}%")
    print("\nlectura: 'plano%' y 'máxinv' = FIABLES (¿controla el inventario?). 'PnL' = cota superior")
    print("(asume ganar la cola en cada fill) → el real es una fracción. La comparación 5m vs 15m y")
    print("baseline vs protegido es lo que vale: ¿la protección sube el PnL y en qué timeframe rinde más?")
    for v in ("5m", "15m"):
        b, pr = agg[(v, "base")], agg[(v, "prot")]
        if b["n"] and pr["n"]:
            d = pr["pnl"] / pr["n"] - b["pnl"] / b["n"]
            print(f"  Δ protección {v}: {d:+.2f}pp/ventana "
                  f"({'ayuda' if d > 0 else 'no ayuda'})")


if __name__ == "__main__":
    try: sys.stdout.reconfigure(encoding="utf-8")
    except Exception: pass
    main()
