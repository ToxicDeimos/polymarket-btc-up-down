"""
ema_combo.py — ¿la EMA9 (taker) se vuelve +EV si la COMBINAMOS con acción del precio / imbalance del libro /
confirmación múltiple? Idea del usuario. Prior: cada señal por separado es plana (mercado eficiente); combinar
planas suele solo reducir muestra. PERO si hay interacción no-lineal (EMA solo predice cuando el libro/momentum
confirma), aparecería aquí. Universo completo de ventanas favorito 62-82¢, sin look-ahead (e=ws+180), CLOB,
neto de fee, train/test.

    cd ~/polymarket-btc-up-down/research && python3 ema_combo.py
"""
import csv, os, sys, glob, bisect

DIR = os.path.join(os.path.dirname(__file__), "lab")
KL  = os.path.join(DIR, "klines_mw.csv")
LO, HI = 0.62, 0.82; ENTRY = 240; TOL = 90


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

    # extraer favorito 62-82¢ + tamaños del libro (para imbalance) a 240s
    snap = {}
    for path in sorted(glob.glob(os.path.join(DIR, "books_*.csv"))):
        with open(path, encoding="utf-8") as fh:
            rd = csv.reader(fh); next(rd, None)
            for row in rd:
                if len(row) < 12 or not row[1].startswith("btc-updown-5m-") or not row[10]: continue
                try:
                    ts = int(row[0]); ws = int(row[1].split("-")[-1])
                    ask = float(row[10]); bs = float(row[5]) if row[5] else 0.0; as_ = float(row[11]) if row[11] else 0.0
                except Exception: continue
                g = abs(ts - (ws + ENTRY))
                if g <= TOL:
                    e = snap.setdefault(row[1], {"cid": row[2], "ws": ws}); cur = e.get(row[3])
                    if cur is None or g < cur[0]: e[row[3]] = (g, ask, bs, as_)
    rows = []
    for slug, e in snap.items():
        if "Up" not in e or "Down" not in e: continue
        win = reso.get(e["cid"])
        if win not in ("Up", "Down"): continue
        au, ad = e["Up"][1], e["Down"][1]
        fav = "Up" if au > ad else "Down"; ask = max(au, ad)
        if not (LO <= ask <= HI): continue
        ws = e["ws"]; ek = ws + ENTRY - 60
        px = kat(kl, ek); opn = kat(kl, ws - 60); e9 = kat(E9, ek); e21 = kat(E21, ek)
        if None in (px, opn, e9, e21): continue
        hi5 = max((kat(kl, ek - i * 60) or -1e9) for i in range(1, 6))
        sgn = 1 if fav == "Up" else -1
        # estocástico %K sobre varios periodos (rango de closes; sin look-ahead), sign-ajustado al favorito
        def stoch(N):
            c = [kat(kl, ek - i * 60) for i in range(N)]
            if any(x is None for x in c): return None
            hh, ll = max(c), min(c)
            pk = (px - ll) / (hh - ll) * 100 if hh > ll else 50.0
            return pk if fav == "Up" else 100 - pk
        st = {N: stoch(N) for N in (9, 14, 21)}
        if any(v is None for v in st.values()): continue
        stoch_fav = st[14]
        # Bollinger(20,2): %B (posición en las bandas) + anchura (volatilidad)
        c20 = [kat(kl, ek - i * 60) for i in range(20)]
        if any(x is None for x in c20): continue
        m = sum(c20) / 20; sd = (sum((x - m) ** 2 for x in c20) / 20) ** 0.5
        up, lo = m + 2 * sd, m - 2 * sd
        pb = (px - lo) / (up - lo) if up > lo else 0.5
        pb_fav = pb if fav == "Up" else 1 - pb        # >1 = favorito por encima de la banda (extremo)
        bw = (up - lo) / m * 1e4 if m else 0          # anchura relativa (bps) = volatilidad
        bs, as_ = e[fav][2], e[fav][3]
        imb = bs / (bs + as_) if (bs + as_) > 0 else 0.5      # >0.5 = presión compradora en el favorito
        rows.append({
            "won": 1 if fav == win else 0, "ask": ask, "ws": ws,
            "marg": sgn * (px - e9) / e9 * 1e4,
            "mom": sgn * (px - opn) / opn * 1e4,
            "trend2": ((px > e9) == (fav == "Up")) and ((px > e21) == (fav == "Up")),
            "brk": (px > opn) == (fav == "Up") and px >= hi5,
            "imb": imb, "stoch": stoch_fav, "st9": st[9], "st14": st[14], "st21": st[21],
            "pb": pb_fav, "bw": bw,
        })
    n = len(rows); tss = sorted(r["ws"] for r in rows); mid = tss[n // 2]
    def wr(rs): return sum(x["won"] for x in rs) / len(rs) * 100 if rs else float("nan")
    def net(rs): return sum(x["won"] - x["ask"] - fee(x["ask"]) for x in rs) / len(rs) * 100 if rs else float("nan")
    def rep(lab, cond):
        rs = [r for r in rows if cond(r)]
        if len(rs) < 30: print(f"   {lab:<28} n={len(rs)} (pocos)"); return
        tr = [r for r in rs if r["ws"] < mid]; te = [r for r in rs if r["ws"] >= mid]
        g = "✓" if (net(rs) > 0 and net(tr) > 0 and net(te) > 0) else ""
        print(f"   {lab:<28} n={len(rs):>5}  win {wr(rs):>5.1f}%  NETO {net(rs):>+6.2f}pp  (tr {net(tr):>+5.1f}/te {net(te):>+5.1f}) {g}")

    print(f"universo favorito 62-82¢: {n}\n")
    rep("EMA fuerte (base)", lambda r: r["marg"] >= 1.5)
    rep("EMA + imbalance>0.55", lambda r: r["marg"] >= 1.5 and r["imb"] >= 0.55)
    rep("EMA + imbalance>0.65", lambda r: r["marg"] >= 1.5 and r["imb"] >= 0.65)
    rep("EMA + momentum fuerte", lambda r: r["marg"] >= 1.5 and r["mom"] >= 3)
    rep("EMA + trend2 (2 EMAs)", lambda r: r["marg"] >= 1.5 and r["trend2"])
    rep("EMA + breakout", lambda r: r["marg"] >= 1.5 and r["brk"])
    rep("EMA + imb + momentum", lambda r: r["marg"] >= 1.5 and r["imb"] >= 0.55 and r["mom"] >= 3)
    print("\n   ── ESTOCÁSTICO: ROBUSTEZ (periodo × umbral, SOLO, sin EMA) ──")
    for key, lab in (("st9", "stoch9"), ("st14", "stoch14"), ("st21", "stoch21")):
        for thr in (70, 75, 80, 85):
            rep(f"{lab}>{thr}", lambda r, k=key, t=thr: r[k] >= t)
        print()
    print("   ── BOLLINGER %B (posición en bandas) ──")
    for thr in (0.8, 0.9, 1.0):
        rep(f"%B>{thr}", lambda r, t=thr: r["pb"] >= t)
    bwmed = sorted(r["bw"] for r in rows)[len(rows) // 2]
    print(f"\n   ── VOLATILIDAD (anchura Bollinger, mediana {bwmed:.0f}bps): stoch14>80 filtrado por vol ──")
    rep("stoch14>80 + vol BAJA", lambda r: r["st14"] >= 80 and r["bw"] < bwmed)
    rep("stoch14>80 + vol ALTA", lambda r: r["st14"] >= 80 and r["bw"] >= bwmed)
    print("\n   ── combos ──")
    rep("stoch14>80 + %B>0.9", lambda r: r["st14"] >= 80 and r["pb"] >= 0.9)
    rep("EMA + stoch14>80 + imb>0.55", lambda r: r["marg"] >= 1.5 and r["st14"] >= 80 and r["imb"] >= 0.55)
    print("\n→ si alguna combo sale NETO>0 y generaliza (✓) → hay interacción, la idea del usuario vale.")
    print("  si todas siguen ≤0 → combinar señales planas no crea edge (mercado eficiente al taker).")


if __name__ == "__main__":
    try: sys.stdout.reconfigure(encoding="utf-8")
    except Exception: pass
    main()
