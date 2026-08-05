"""
strategy_lab.py — backtestea una BATERÍA de estrategias a la vez sobre el UNIVERSO de ventanas (favorito
62-82¢ del libro, cobertura completa, survivorship-free), net-of-fee, train/test. Rankea por NETO_test.
Incluye TAKER (señales de precio/estructura, para confirmar eficiencia) y MAKER (bids límite, donde opera
13mm — la esperanza real). Sin look-ahead en señales (≤240s); los fills maker usan el futuro de la ventana
(correcto: un límite se llena en el futuro). Fee taker 0.07·p·(1−p); maker 0.

    cd ~/polymarket-btc-up-down/research && python3 strategy_lab.py
Extensible: añade estrategias en STRATS. Reusa books_*.csv, klines_mw.csv, cachés de resolución.
"""
import csv, os, sys, glob, bisect

DIR = os.path.join(os.path.dirname(__file__), "lab")
KL  = os.path.join(DIR, "klines_mw.csv")
LO, HI = 0.62, 0.82; WLEN = 300


def fee(p): return 0.07 * p * (1 - p)


def load_reso():
    d = {}
    for fn in ("clob_reso_mw.csv", "clob_reso_win.csv", "clob_reso_tape.csv", "clob_reso_uni.csv"):
        p = os.path.join(DIR, fn)
        if os.path.exists(p):
            for r in csv.DictReader(open(p, encoding="utf-8")):
                if r["winner"]: d[r["cid"]] = r["winner"]
    return d


def main():
    reso = load_reso()
    kl = {}
    for ln in open(KL, encoding="utf-8"):
        a = ln.split(",")
        try: kl[int(a[0])] = float(a[1])
        except Exception: pass
    kmins = sorted(kl); k = 2 / 10; E9 = {}; k21 = 2 / 22; E21 = {}; p9 = p21 = None
    for m in kmins:
        p9 = kl[m] if p9 is None else kl[m] * k + p9 * (1 - k); E9[m] = p9
        p21 = kl[m] if p21 is None else kl[m] * k21 + p21 * (1 - k21); E21[m] = p21
    def kat(dic, m):
        i = bisect.bisect_right(kmins, m) - 1
        return dic[kmins[i]] if i >= 0 else None

    # acumuladores por estrategia: lista de (won, net, ws)
    res = {}
    def rec(name, won, net, ws):
        res.setdefault(name, []).append((won, net, ws))

    def snap_at(series, t, tol=90):        # (bid, ask) más cercano a t
        best = None
        for (ts, bid, ask) in series:
            g = abs(ts - t)
            if g <= tol and (best is None or g < best[0]): best = (g, bid, ask)
        return (best[1], best[2]) if best else None

    n_win = 0
    for path in sorted(glob.glob(os.path.join(DIR, "books_*.csv"))):
        W = {}
        with open(path, encoding="utf-8") as fh:
            rd = csv.reader(fh); next(rd, None)
            for row in rd:
                if len(row) < 11 or not row[1].startswith("btc-updown-5m-"): continue
                try:
                    ts = int(row[0]); ws = int(row[1].split("-")[-1])
                    bid = float(row[4]) if row[4] else None; ask = float(row[10]) if row[10] else None
                except Exception: continue
                if bid is None or ask is None: continue
                w = W.setdefault(row[1], {"cid": row[2], "ws": ws, "Up": [], "Down": []})
                w[row[3]].append((ts, bid, ask))
        for slug, w in W.items():
            win = reso.get(w["cid"])
            if win not in ("Up", "Down") or not w["Up"] or not w["Down"]: continue
            ws = w["ws"]
            s240U = snap_at(w["Up"], ws + 240); s240D = snap_at(w["Down"], ws + 240)
            s180U = snap_at(w["Up"], ws + 180); s180D = snap_at(w["Down"], ws + 180)
            if None in (s240U, s240D, s180U, s180D): continue
            fav = "Up" if s240U[1] > s240D[1] else "Down"; ud = "Down" if fav == "Up" else "Up"
            fav_ask = s240U[1] if fav == "Up" else s240D[1]; ud_ask = s240D[1] if fav == "Up" else s240U[1]
            if not (LO <= fav_ask <= HI): continue
            n_win += 1
            # precio/estructura a 240s (sin look-ahead): vela [ws+180,ws+240)
            ek = ws + 180
            px = kat(kl, ek); opn = kat(kl, ws - 60); e9 = kat(E9, ek); e21 = kat(E21, ek)
            p1 = kat(kl, ek - 60)
            hi5 = max((kat(kl, ek - i * 60) or -1e9) for i in range(1, 6))   # máx de los 5 min previos
            if None in (px, opn, e9, e21, p1): continue
            sgnf = 1 if fav == "Up" else -1
            marg = sgnf * (px - e9) / e9 * 1e4
            wonf = 1 if fav == win else 0; wonu = 1 - wonf
            # futuro de asks por lado tras 180s (para fills maker): touch=toca una vez, robust=≥2 snapshots
            futs = {s: [a for (t, b, a) in w[s] if ws + 180 < t < ws + WLEN] for s in ("Up", "Down")}
            fmin = {s: (min(futs[s]) if futs[s] else 1e9) for s in ("Up", "Down")}
            def touch(s, bid): return fmin[s] <= bid
            def robust(s, bid): return sum(1 for a in futs[s] if a <= bid) >= 2

            # ── TAKER (compra al ask a 240s) ──
            rec("T_blind_fav", wonf, wonf - fav_ask - fee(fav_ask), ws)
            rec("T_underdog", wonu, wonu - ud_ask - fee(ud_ask), ws)               # fade: underdog al ask (base)
            if marg >= 1.5:
                rec("T_ema_fav", wonf, wonf - fav_ask - fee(fav_ask), ws)
            if (px > opn) == (fav == "Up") and abs(px - opn) / opn * 1e4 >= 2:
                rec("T_momentum", wonf, wonf - fav_ask - fee(fav_ask), ws)
            if ((px > e9) == (fav == "Up")) and ((px > e21) == (fav == "Up")):
                rec("T_trend2", wonf, wonf - fav_ask - fee(fav_ask), ws)
            if (px > opn) == (fav == "Up") and px >= hi5:
                rec("T_break", wonf, wonf - fav_ask - fee(fav_ask), ws)
            if 0.62 <= fav_ask < 0.68:
                rec("T_fav_lowband", wonf, wonf - fav_ask - fee(fav_ask), ws)

            # ── MAKER: post a 180s, fill DESPUÉS de 180s (coherente, sin look-ahead) ──
            fav180 = "Up" if s180U[1] > s180D[1] else "Down"; ud180 = "Down" if fav180 == "Up" else "Up"
            fav180a = s180U[1] if fav180 == "Up" else s180D[1]
            ud180a = s180D[1] if fav180 == "Up" else s180U[1]
            wonf180 = 1 if fav180 == win else 0; wonu180 = 1 - wonf180
            if LO <= fav180a <= HI:
                for d in (0.02, 0.04):
                    if touch(fav180, fav180a - d): rec(f"M_fav_{int(d*100)}c", wonf180, wonf180 - (fav180a - d), ws)
                if fav180a >= 0.76 and touch(fav180, fav180a - 0.02):
                    rec("M_fav_deep_2c", wonf180, wonf180 - (fav180a - 0.02), ws)
            # underdog: barrido de profundidad + fill robusto + por banda
            for d in (0.01, 0.02, 0.03, 0.04):
                b = ud180a - d
                if b > 0 and touch(ud180, b): rec(f"M_ud_{int(d*100)}c", wonu180, wonu180 - b, ws)
            b2 = ud180a - 0.02
            if b2 > 0 and robust(ud180, b2): rec("M_ud_2c_robust", wonu180, wonu180 - b2, ws)
            if b2 > 0 and touch(ud180, b2):
                rec("M_ud_deep" if ud180a < 0.30 else "M_ud_shallow", wonu180, wonu180 - b2, ws)
            # market-making DOBLE CARA
            netmm = 0.0; filled = 0
            for s in ("Up", "Down"):
                b = (s180U[1] if s == "Up" else s180D[1]) - 0.02
                if touch(s, b):
                    netmm += (1 if s == win else 0) - b; filled += 1
            if filled: rec("M_bothsides_2c", None, netmm, ws)

    # ── informe ──
    allws = sorted({r[2] for L in res.values() for r in L}); mid = allws[len(allws) // 2] if allws else 0
    def stats(L):
        n = len(L); nt = [r for r in L if r[2] < mid]; ne = [r for r in L if r[2] >= mid]
        wr = sum(r[0] for r in L if r[0] is not None) / max(1, sum(1 for r in L if r[0] is not None)) * 100
        net = sum(r[1] for r in L) / n * 100
        net_tr = sum(r[1] for r in nt) / len(nt) * 100 if nt else float("nan")
        net_te = sum(r[1] for r in ne) / len(ne) * 100 if ne else float("nan")
        return n, wr, net, net_tr, net_te
    print(f"universo: {n_win} ventanas favorito 62-82¢\n")
    print(f"{'estrategia':<16}{'n/fills':>9}{'win':>7}{'NETO':>9}{'tr':>8}{'te':>8}  gen")
    print("-" * 62)
    rows = [(name, *stats(L)) for name, L in res.items()]
    rows.sort(key=lambda x: x[4], reverse=True)     # por NETO_test
    for name, n, wr, net, ntr, nte in rows:
        gen = "✓" if (net > 0 and ntr > 0 and nte > 0) else ""
        wrs = f"{wr:>5.0f}%" if wr == wr else "   —"
        print(f"{name:<16}{n:>9}{wrs}{net:>+8.2f}{ntr:>+8.1f}{nte:>+8.1f}  {gen}")
    print("\n→ ranking por NETO_test. Las ✓ (NETO>0 y generaliza train Y test) son las que merecen paper.")


if __name__ == "__main__":
    try: sys.stdout.reconfigure(encoding="utf-8")
    except Exception: pass
    main()
