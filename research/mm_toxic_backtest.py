"""
mm_toxic_backtest.py — MM con SKEW de inventario + PROTECCIÓN DE FLUJO TÓXICO, sobre la cinta REAL,
comparando 5m vs 15m. La pieza NUEVA que el simulador ingenuo no tenía y un MM real SÍ usa:
cuando entra un trade agresor GRANDE (size >= percentil), RETIRO el lado que me llenaría en adverso
durante COOLDOWN s (el flujo informado llega en oleadas → esquivo las réplicas, no la primera copia).

Además compara el SETTLEMENT del inventario sobrante: HOLD (aguantar a resolución) vs FLAT (deshacerlo
al bid en el cierre pagando fee taker = irse a casa plano). El diagnóstico decía que TODA la pérdida es
la cola direccional del inventario atascado, no la captura del spread → FLAT debería borrarla.

Y BARRE LA PROFUNDIDAD del quote (my_bid = best_bid − DEPTH): los ganadores (13mm-wrench, winner_reverse)
NO cotizan al toque, compran ~4¢ por debajo → solo se llenan en OVERSHOOTS que revierten, no en la deriva
adversa. Pregunta decisiva: ¿a más DEPTH el favorito comprado hundido gana MÁS que su precio (edge real) o
gana ~su precio (el dump era información → no hay MM para nosotros)? Settlement HOLD (los ganadores aguantan).

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


def run_mm(book, trs, toxic_size, cooldown, depth=0.0):
    """book: sorted [(ts,bid,ask)]; trs: sorted [(ts,side,price,size)]. toxic_size=None → sin protección.
    depth = cuántos ¢ POR DEBAJO del bid (y por encima del ask) descanso → solo me lleno en OVERSHOOTS.
    Devuelve las piezas crudas (cash, inv…); el settlement (hold vs flat) se calcula fuera."""
    inv = 0; cash = 0.0; buys = sells = rt = 0; maxinv = 0; buysum = 0.0
    bid_pull = ask_pull = 0
    bi = 0; bid = ask = my_bid = my_ask = None

    def requote():
        nonlocal my_bid, my_ask
        if bid is None or ask is None: return
        my_bid = round(bid - depth - SKEW * inv, 2); my_ask = round(ask + depth - SKEW * inv, 2)

    for ts, side, price, size in trs:
        while bi < len(book) and book[bi][0] <= ts:
            _, bid, ask = book[bi]; bi += 1
        requote()
        if my_bid is not None and my_ask is not None:
            # fill bajo el estado de retirada actual (la 1ª copia tóxica NO se puede esquivar; sí las réplicas)
            if side == "SELL" and price <= my_bid and inv < MAXINV and ts >= bid_pull:
                inv += 1; cash -= my_bid; buysum += my_bid; buys += 1; maxinv = max(maxinv, inv); requote()
            elif side == "BUY" and price >= my_ask and inv > 0 and ts >= ask_pull:
                inv -= 1; cash += my_ask; sells += 1; rt += 1; requote()
        if toxic_size is not None and size >= toxic_size:   # protege los prints siguientes
            if side == "SELL": bid_pull = ts + cooldown     # venta informada → dejo de comprar
            elif side == "BUY": ask_pull = ts + cooldown    # compra informada → dejo de vender
    return dict(cash=cash, buys=buys, sells=sells, rt=rt, end_inv=inv, maxinv=maxinv, buysum=buysum)


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
        jobs.append({"cid": w["cid"], "v": v, "ws": ws, "fav": fav, "fav_ask": fav_ask, "book": book, "trs": trs})

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

    # PASO 2: resolver una vez (winner por ventana) y BARRER LA PROFUNDIDAD. my_bid = best_bid − DEPTH:
    # a más DEPTH solo me lleno cuando un trade IMPRIME ese overshoot (dump que sobrepasa y suele revertir).
    # Settlement HOLD (los ganadores aguantan a resolución). DEPTH=0 = al toque = control (≈ −0,43/−7,21).
    print(f"\nresolviendo {len(jobs)} ventanas por CLOB (cacheado)…")
    done = 0
    for j in jobs:
        w = resolve(j["cid"]); j["won"] = (1 if j["fav"] == w else 0) if w in ("Up", "Down") else None
        done += 1
        if done % 500 == 0: print(f"   … {done}/{len(jobs)}")
    jobs = [j for j in jobs if j["won"] is not None]
    print(f"  ventanas con ganador: {len(jobs)}")

    DEPTHS = (0.0, 0.02, 0.03, 0.04, 0.05)
    # recolectar por (v, depth) la lista cruda: (ws, pnl, buys, buysum, won)
    data = {(v, d): [] for v in ("5m", "15m") for d in DEPTHS}
    for j in jobs:
        won = j["won"]; fa = j["fav_ask"]
        for d in DEPTHS:
            r = run_mm(j["book"], j["trs"], None, COOLDOWN, d)
            pnl = (r["cash"] + r["end_inv"] * won + (r["buys"] + r["sells"]) * reb(fa)) * 100
            data[(j["v"], d)].append((j["ws"], pnl, r["buys"], r["buysum"], won))

    def mean(xs): return sum(xs) / len(xs) if xs else 0.0
    def median(xs):
        s = sorted(xs); n = len(s)
        return 0.0 if n == 0 else (s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2)
    mids = {v: sorted(j["ws"] for j in jobs if j["v"] == v)[max(0, sum(1 for j in jobs if j["v"] == v) // 2 - 1)]
            for v in ("5m", "15m") if any(j["v"] == v for j in jobs)}

    # TABLA 1 — barrido (headline)
    print("\n" + "=" * 90)
    print("  MM skew + HOLD · BARRIDO DE PROFUNDIDAD (my_bid = best_bid − DEPTH) · PnL = COTA SUPERIOR")
    print("=" * 90)
    print(f"{'':5}{'DEPTH':>6}{'N':>7}{'con-fill':>9}{'buys':>7}{'precio':>8}{'fav-gana%':>11}{'PnL/vent':>11}{'PnL/fill':>10}")
    for v in ("5m", "15m"):
        for d in DEPTHS:
            rows = data[(v, d)]; n = len(rows)
            if not n: continue
            fill = [r for r in rows if r[2] > 0]; fl = len(fill)
            tb = sum(r[2] for r in fill); bs = sum(r[3] for r in fill)
            wr = 100 * sum(r[4] for r in fill) / fl if fl else 0
            print(f"{v:5}{d*100:>5.0f}¢{n:>7}{fl:>9}{tb:>7}{(bs/tb if tb else 0):>8.3f}{wr:>10.0f}%"
                  f"{mean([r[1] for r in rows]):>+10.2f}{(mean([r[1] for r in fill]) if fl else 0):>+10.2f}")
        print()

    # GATE 1 — train/test temporal
    print("=" * 90)
    print("  GATE 1 — TRAIN/TEST TEMPORAL (parte por fecha; el edge debe aguantar en la mitad OOS)")
    print("=" * 90)
    print(f"{'':5}{'DEPTH':>6}{'N_train':>9}{'PnL_train':>11}{'N_test':>9}{'PnL_test':>11}{'  OOS':>7}")
    for v in ("5m", "15m"):
        for d in DEPTHS:
            rows = data[(v, d)]
            if not rows: continue
            tr = [p for ws, p, *_ in rows if ws < mids[v]]; te = [p for ws, p, *_ in rows if ws >= mids[v]]
            mtr, mte = mean(tr), mean(te)
            ok = "✓" if (mtr > 0 and mte > 0) else ("✗" if mte < 0 else "≈")
            print(f"{v:5}{d*100:>5.0f}¢{len(tr):>9}{mtr:>+10.2f}{len(te):>9}{mte:>+10.2f}{ok:>6}")
        print()

    # GATE 2 — amplitud (¿broad o 4 pelotazos?)
    print("=" * 90)
    print("  GATE 2 — AMPLITUD (¿el +EV es broad o viene de unas pocas ventanas?)")
    print("=" * 90)
    print(f"{'':5}{'DEPTH':>6}{'PnL/vent':>10}{'PnL(−top1%)':>13}{'mediana-fill':>14}{'%fill>0':>9}")
    for v in ("5m", "15m"):
        for d in DEPTHS:
            rows = data[(v, d)]
            if not rows: continue
            pnls = [r[1] for r in rows]; n = len(pnls)
            k = max(1, int(0.01 * n)); rest = sorted(pnls, reverse=True)[k:]
            trim = mean(rest)
            fillp = [r[1] for r in rows if r[2] > 0]
            medf = median(fillp); pos = 100 * sum(1 for p in fillp if p > 0) / len(fillp) if fillp else 0
            print(f"{v:5}{d*100:>5.0f}¢{mean(pnls):>+9.2f}{trim:>+12.2f}{medf:>+13.2f}{pos:>8.0f}%")
        print()
    print("VEREDICTO: edge REAL sólo si en GATE 1 'PnL_test' sigue + (✓) Y en GATE 2 'PnL(−top1%)' sigue + con")
    print("'mediana-fill' ≥ 0 (no dependemos de 4 pelotazos). Si test<0 o el trim se hunde → sobreajuste, se cierra.")
    print("Recuerda: todo es COTA SUPERIOR (gana la cola); el real es una fracción. Elegir la profundidad más")
    print("robusta (misma señal en train y test, broad), no la de mayor PnL — esa es sobreajustar la muestra.")


if __name__ == "__main__":
    try: sys.stdout.reconfigure(encoding="utf-8")
    except Exception: pass
    main()
