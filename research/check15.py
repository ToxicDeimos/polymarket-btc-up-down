"""
check15.py — AUTOPSIA del único candidato que ha pasado las cuatro puertas como taker (volskew TEST 2):
15m·TWAP60, comprar el lado que el modelo de volatilidad dice infravalorado en >10pp → EV +2,5 a +3,2.
Antes de montar NADA hay que intentar tumbarlo. Seis formas de que sea mentira:

 A) CONCENTRACIÓN — que sean tres días buenos. Semana a semana, y EV quitando el mejor día y la mejor semana.
 B) PROFUNDIDAD — el 15m es fino. Si el mejor ask tiene $20 detrás, el edge es real y NO SIRVE. Mediana de
    tamaño al ask, EV solo con profundidad ≥N, y EV ponderado por el tamaño realmente disponible.
 C) ROBUSTEZ — σ a 10/30/60 min × umbral 8/10/12/15/20pp. Si solo vive en 30 min y >10pp, está ajustado.
 D) UNA OPERACIÓN POR VENTANA — pooling t+30/50/70 cuenta la misma ventana 3 veces. Lo honesto: barrer del
    20% al 75% de la ventana y tomar el PRIMER disparo. Ese es el backtest que se parece al bot.
 E) RETRATO — ¿qué son estos casos? precio, spread, margen $, σi/σr, hora UTC. Si son todos a las 3 UTC de
    los domingos, es otra cosa disfrazada.
 F) CONTROL PERMUTADO — mismo número de operaciones, mismos instantes, pero eligiendo el lado al azar.
    El EV tiene que desaparecer. Si el azar también gana, el dinero venía del ask, no del modelo.

    cd ~/polymarket-btc-up-down/research && python3 check15.py
"""
import csv, os, sys, glob, json, time, math, bisect, random, urllib.request

DIR = os.path.join(os.path.dirname(__file__), "lab")
CACHE = os.path.join(DIR, "clob_reso_mmtoxic.csv")
T_TWAP = 1786060800
GRID, LOOKS = 10, {"10min": 60, "30min": 180, "60min": 360}
THR = (0.08, 0.10, 0.12, 0.15, 0.20)
SCAN = [0.20 + 0.05 * i for i in range(12)]      # 20% … 75%
L15 = 60          # 15m siempre TWAP-60 desde el 7-ago
WLEN = 900


def get(url, tries=2):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "chk15/1.0"})
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
def mean(xs): return sum(xs) / len(xs) if xs else float("nan")
def med(xs):
    s = sorted(xs); return s[len(s) // 2] if s else float("nan")
def cdf(x): return 0.5 * (1 + math.erf(x / math.sqrt(2)))
def week(ts): return time.strftime("%Y-%U", time.gmtime(ts))
def day(ts): return time.strftime("%Y-%m-%d", time.gmtime(ts))


def load_spot():
    out = []
    for path in sorted(glob.glob(os.path.join(DIR, "spot_*.csv"))):
        with open(path, encoding="utf-8") as fh:
            rd = csv.reader(fh); next(rd, None)
            for row in rd:
                try: out.append((int(row[0]), float(row[1])))
                except Exception: continue
    out.sort(); return [t for t, _ in out], [p for _, p in out]


def load_books15():
    """solo 15m. ts0 slug1 cid2 side3 b1=4 bs1=5 … a1=10 as1=11"""
    tmp = {}
    for path in sorted(glob.glob(os.path.join(DIR, "books_*.csv"))):
        with open(path, encoding="utf-8") as fh:
            rd = csv.reader(fh); next(rd, None)
            for row in rd:
                if len(row) < 12 or not row[1].startswith("btc-updown-15m-") or row[3] not in ("Up", "Down"):
                    continue
                try:
                    ts = int(row[0]); bid = float(row[4]) if row[4] else None
                    ask = float(row[10]) if row[10] else None
                    asz = float(row[11]) if row[11] else 0.0
                except Exception: continue
                if ask is None or bid is None: continue
                w = tmp.setdefault(row[1], {"cid": row[2], "Up": [], "Down": []})
                w[row[3]].append((ts, ask, bid, asz))
    W = {}
    for slug, w in tmp.items():
        if not w["Up"] or not w["Down"]: continue
        ws = int(slug.split("-")[-1])
        if ws < T_TWAP: continue
        W[slug] = {"cid": w["cid"], "ws": ws}
        for s in ("Up", "Down"):
            r = sorted(w[s])
            W[slug][s] = ([x[0] for x in r], [x[1] for x in r], [x[2] for x in r], [x[3] for x in r])
    return W


def report(rows, lab, wide=False):
    """n, ask, acierto, EV, tr, te, -top1%, semanas+, z binomial."""
    if len(rows) < 30:
        print(f"  {lab:>30}{'(pocos)':>9}"); return None
    def pnl(r): return r["won"] - r["ask"] - fee(r["ask"])
    def ev(s): return 100 * mean([pnl(x) for x in s])
    mid = sorted(r["ws"] for r in rows)[len(rows) // 2]
    tr = [r for r in rows if r["ws"] < mid]; te = [r for r in rows if r["ws"] >= mid]
    ps = sorted(rows, key=pnl); k = max(1, int(len(rows) * 0.01))
    byw = {}
    for r in rows: byw.setdefault(week(r["ws"]), []).append(r)
    wt = [s for s in byw.values() if len(s) >= 10]
    wp = sum(1 for s in wt if ev(s) > 0)
    pa = mean([r["ask"] for r in rows]); g = mean([r["won"] for r in rows])
    se = math.sqrt(max(pa * (1 - pa), 1e-9) / len(rows))
    z = (g - pa) / se
    print(f"  {lab:>30}{len(rows):>7}{pa:>7.3f}{100*g:>6.0f}%{ev(rows):>+8.2f}"
          f"{(ev(tr) if len(tr)>=10 else float('nan')):>+7.2f}{(ev(te) if len(te)>=10 else float('nan')):>+7.2f}"
          f"{100*mean([pnl(x) for x in ps[:-k]]):>+8.2f}{f'{wp}/{len(wt)}':>7}{z:>+7.2f}")
    return ev(rows)


HDR = (f"  {'caso':>30}{'n':>7}{'ask':>7}{'gana%':>7}{'EV':>8}{'tr':>7}{'te':>7}{'-top1%':>8}"
       f"{'sem+':>7}{'z':>7}")


def main():
    sts, spx = load_spot(); W = load_books15()
    print(f"ventanas 15m (régimen TWAP): {len(W)} · ticks spot: {len(sts)}")

    g0 = (sts[0] // GRID) * GRID; g1 = (sts[-1] // GRID) * GRID
    ng = (g1 - g0) // GRID + 1
    gpx = [None] * ng
    for i in range(ng):
        t = g0 + i * GRID
        j = bisect.bisect_right(sts, t) - 1
        if j >= 0 and t - sts[j] <= GRID: gpx[i] = spx[j]
    S = [0.0] * (ng + 1); C = [0] * (ng + 1)
    for i in range(1, ng):
        d2, c = 0.0, 0
        if gpx[i] is not None and gpx[i - 1] is not None:
            d2 = (gpx[i] - gpx[i - 1]) ** 2; c = 1
        S[i + 1] = S[i] + d2; C[i + 1] = C[i] + c

    def sigma(t, look):
        i = int((t - g0) // GRID)
        if i < 2: return None
        lo = max(1, i - look); c = C[i] - C[lo]
        if c < look * 0.6: return None
        v = (S[i] - S[lo]) / (c * GRID)
        return math.sqrt(v) if v > 0 else None

    def avg(a, b):
        lo = bisect.bisect_left(sts, a); hi = bisect.bisect_right(sts, b)
        if hi - lo < 2: return None
        seg = spx[lo:hi]; return sum(seg) / len(seg)

    def le(t, tol):
        i = bisect.bisect_right(sts, t) - 1
        return spx[i] if (i >= 0 and t - sts[i] <= tol) else None

    reso = {}
    for fn in ("clob_reso_mmtoxic.csv", "clob_reso_mmlogs.csv", "clob_reso_mw.csv",
               "clob_reso_win.csv", "clob_reso_tape.csv", "clob_reso_uni.csv"):
        p = os.path.join(DIR, fn)
        if os.path.exists(p):
            for r in csv.DictReader(open(p, encoding="utf-8")):
                if r.get("winner"): reso[r["cid"]] = r["winner"]

    def resolve(cid):
        if cid in reso: return reso[cid]
        w = winner_clob(cid); time.sleep(0.1)
        if w:
            reso[cid] = w; nf = not os.path.exists(CACHE)
            with open(CACHE, "a", newline="", encoding="utf-8") as fo:
                cw = csv.writer(fo)
                if nf: cw.writerow(["cid", "winner"])
                cw.writerow([cid, w])
        return w

    # ---------- barrido: cada ventana, cada instante del 20% al 75% ----------
    SNAP = []          # todas las observaciones, con las tres σ
    done = 0
    for slug, w in W.items():
        ws = w["ws"]; close = ws + WLEN
        ref = avg(ws - L15, ws)
        if ref is None: continue
        done += 1
        if done % 2000 == 0: print(f"   … {done}")
        win = resolve(w["cid"])
        if win not in ("Up", "Down"): continue
        wup = 1 if win == "Up" else 0
        for f in SCAN:
            t = ws + int(f * WLEN)
            cur = le(t, 12)
            if cur is None: continue
            tau = (close - t) - 2 * L15 / 3.0
            if tau <= 5: continue
            sgs = {k: sigma(t, lk) for k, lk in LOOKS.items()}
            if sgs["30min"] is None: continue

            def fresh(X):
                tss, asks, bids, aszs = w[X]; k = bisect.bisect_left(tss, t)
                if k < len(tss) and tss[k] <= t + 15 and tss[k] < close - 20:
                    a, b, s = asks[k], bids[k], aszs[k]
                    if 0 < b < a < 1: return a, b, s
                return None, None, None
            aU, bU, sU = fresh("Up"); aD, bD, sD = fresh("Down")
            if aU is None or aD is None: continue
            pm = ((bU + aU) / 2 + (1 - (bD + aD) / 2)) / 2
            if not (0.02 < pm < 0.98): continue
            m = cur - ref
            SNAP.append({"ws": ws, "f": f, "t": t, "pm": pm, "m": m, "tau": tau, "wup": wup,
                         "aU": aU, "aD": aD, "sU": sU, "sD": sD, "spU": aU - bU, "spD": aD - bD,
                         "sg": sgs, "hour": int(time.strftime("%H", time.gmtime(ws)))})
    print(f"observaciones: {len(SNAP)} · ventanas resueltas: {len(set(o['ws'] for o in SNAP))}")

    def pmod(o, look):
        s = o["sg"].get(look)
        if s is None or s <= 0: return None
        return cdf(o["m"] / (s * math.sqrt(o["tau"])))

    def trade(o, look, thr):
        """lado que el modelo dice barato por ≥thr, o None."""
        p = pmod(o, look)
        if p is None: return None
        if p - o["pm"] >= thr:
            return {"ws": o["ws"], "ask": o["aU"], "won": o["wup"], "sz": o["sU"],
                    "sp": o["spU"], "o": o, "up": True}
        if (1 - p) - (1 - o["pm"]) >= thr:
            return {"ws": o["ws"], "ask": o["aD"], "won": 1 - o["wup"], "sz": o["sD"],
                    "sp": o["spD"], "o": o, "up": False}
        return None

    def first_per_window(look, thr):
        """D) una sola operación por ventana: el PRIMER disparo barriendo hacia delante."""
        best = {}
        for o in sorted(SNAP, key=lambda x: (x["ws"], x["f"])):
            if o["ws"] in best: continue
            tr = trade(o, look, thr)
            if tr: best[o["ws"]] = tr
        return list(best.values())

    # ================= D) UNA OPERACIÓN POR VENTANA =================
    print("\n" + "=" * 104)
    print("  D) UNA OPERACIÓN POR VENTANA (primer disparo entre el 20% y el 75%) — el backtest realista")
    print("=" * 104)
    print(HDR)
    BASE = first_per_window("30min", 0.10)
    report(BASE, "σ30min · umbral 10pp")
    # pooling antiguo, para comparar
    pool = [t for o in SNAP if (t := trade(o, "30min", 0.10))]
    report(pool, "(pooling, cuenta repetida)")

    if len(BASE) < 40:
        print("\n  sin operaciones suficientes con el criterio base — nada que auditar"); return

    # ================= A) CONCENTRACIÓN =================
    print("\n" + "=" * 104)
    print("  A) ¿ES CONCENTRACIÓN? semana a semana y quitando lo mejor")
    print("=" * 104)
    def pnl(r): return r["won"] - r["ask"] - fee(r["ask"])
    byw = {}
    for r in BASE: byw.setdefault(week(r["ws"]), []).append(r)
    print(f"  {'semana':>10}{'n':>7}{'ask':>8}{'gana%':>8}{'EV':>9}{'PnL total':>12}")
    for k in sorted(byw):
        s = byw[k]
        print(f"  {k:>10}{len(s):>7}{mean([r['ask'] for r in s]):>8.3f}"
              f"{100*mean([r['won'] for r in s]):>7.0f}%{100*mean([pnl(r) for r in s]):>+9.2f}"
              f"{sum(pnl(r) for r in s):>+12.2f}")
    byd = {}
    for r in BASE: byd.setdefault(day(r["ws"]), []).append(r)
    tot = sum(pnl(r) for r in BASE)
    dd = sorted(byd.items(), key=lambda kv: -sum(pnl(r) for r in kv[1]))
    print(f"\n  PnL total {tot:+.2f} · mejor día {dd[0][0]} {sum(pnl(r) for r in dd[0][1]):+.2f} "
          f"({100*sum(pnl(r) for r in dd[0][1])/tot if tot else float('nan'):.0f}% del total) · "
          f"top-5 días {100*sum(sum(pnl(r) for r in d[1]) for d in dd[:5])/tot if tot else float('nan'):.0f}%")
    print(HDR)
    report([r for r in BASE if day(r["ws"]) != dd[0][0]], "sin el mejor día")
    wl = sorted(byw.items(), key=lambda kv: -sum(pnl(r) for r in kv[1]))
    report([r for r in BASE if week(r["ws"]) != wl[0][0]], f"sin la mejor semana ({wl[0][0]})")

    # ================= B) PROFUNDIDAD =================
    print("\n" + "=" * 104)
    print("  B) ¿SE PUEDE EJECUTAR? tamaño disponible al mejor ask (shares; $ = shares·precio)")
    print("=" * 104)
    szs = [r["sz"] for r in BASE if r["sz"] is not None]
    if szs:
        szs_s = sorted(szs)
        q = lambda p: szs_s[min(len(szs_s) - 1, int(p * len(szs_s)))]
        print(f"  tamaño al ask: p10 {q(.10):.0f} · mediana {q(.50):.0f} · p90 {q(.90):.0f} shares  "
              f"(≈ ${q(.50)*mean([r['ask'] for r in BASE]):.0f} al precio medio)")
        print(f"  spread del lado comprado: mediana {100*med([r['sp'] for r in BASE]):.1f}¢")
    print(HDR)
    for n0 in (20, 50, 100, 200, 500):
        report([r for r in BASE if (r["sz"] or 0) >= n0], f"solo con ≥{n0} shares al ask")
    for cap in (50, 200):
        sub = [r for r in BASE if (r["sz"] or 0) > 0]
        if sub:
            wsum = sum(min(r["sz"], cap) for r in sub)
            evw = 100 * sum(min(r["sz"], cap) * pnl(r) for r in sub) / wsum if wsum else float("nan")
            print(f"  {f'EV ponderado por tamaño (tope {cap})':>30}{len(sub):>7}{'':>7}{'':>7}{evw:>+8.2f}")

    # ================= C) ROBUSTEZ =================
    print("\n" + "=" * 104)
    print("  C) ROBUSTEZ: ¿vive solo en σ30min y umbral 10pp? (EV · n)")
    print("=" * 104)
    print(f"  {'σ':>10}" + "".join(f"{f'{int(t*100)}pp':>16}" for t in THR))
    for look in ("10min", "30min", "60min"):
        cells = []
        for t in THR:
            s = first_per_window(look, t)
            cells.append(f"{100*mean([pnl(r) for r in s]):+.2f}·{len(s)}" if len(s) >= 30 else "—")
        print(f"  {look:>10}" + "".join(f"{c:>16}" for c in cells))

    # ================= E) RETRATO =================
    print("\n" + "=" * 104)
    print("  E) RETRATO DE LOS CASOS")
    print("=" * 104)
    o = [r["o"] for r in BASE]
    print(f"  precio de mercado: mediana {med([x['pm'] for x in o]):.3f} · ask pagado {med([r['ask'] for r in BASE]):.3f}")
    print(f"  margen |precio−referencia|: mediana ${med([abs(x['m']) for x in o]):.1f}")
    print(f"  momento del disparo: mediana {100*med([x['f'] for x in o]):.0f}% de la ventana")
    print(f"  σ realizada (30min): mediana {med([x['sg']['30min'] for x in o]):.3f} $/√s")
    hh = {}
    for x in o: hh[x["hour"]] = hh.get(x["hour"], 0) + 1
    top = sorted(hh.items(), key=lambda kv: -kv[1])[:5]
    print(f"  horas UTC más frecuentes: " + " · ".join(f"{h:02d}h {c}" for h, c in top) +
          f"  (de {len(hh)} horas distintas)")
    print(HDR)
    for nm, lo, hi in (("precio <0,50", 0, .50), ("precio 0,50-0,65", .50, .65), ("precio >0,65", .65, 1)):
        report([r for r in BASE if lo <= r["o"]["pm"] < hi], f"mercado {nm}")

    # ================= F) CONTROL PERMUTADO =================
    print("\n" + "=" * 104)
    print("  F) CONTROL: mismos instantes, lado AL AZAR (el EV debe desaparecer)")
    print("=" * 104)
    print(HDR)
    rnd = random.Random(7)
    for it in range(3):
        ctl = []
        for r in BASE:
            up = rnd.random() < 0.5
            x = r["o"]
            ctl.append({"ws": x["ws"], "ask": x["aU"] if up else x["aD"],
                        "won": x["wup"] if up else 1 - x["wup"]})
        report(ctl, f"lado al azar (siembra {it+1})")
    # control 2: comprar SIEMPRE el favorito del mercado en esos mismos instantes
    fav = [{"ws": x["ws"], "ask": x["aU"] if x["pm"] > .5 else x["aD"],
            "won": x["wup"] if x["pm"] > .5 else 1 - x["wup"]} for x in o]
    report(fav, "favorito del mercado")

    print("\nLECTURA: el candidato sobrevive solo si (D) aguanta con UNA operación por ventana, (A) no depende")
    print("de un día ni de una semana, (B) sigue en pie exigiendo profundidad real al ask, (C) no se cae al")
    print("mover σ o el umbral, y (F) el azar y el favorito salen planos o negativos. Si falla B, el edge existe")
    print("pero no es cobrable y hay que buscarlo como maker. Si falla A o C, era ajuste.")


if __name__ == "__main__":
    try: sys.stdout.reconfigure(encoding="utf-8")
    except Exception: pass
    main()
