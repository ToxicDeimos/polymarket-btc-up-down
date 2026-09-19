"""
endwindow_lag.py — ¿el mercado va con RETRASO respecto al spot en los últimos segundos de la ventana?
En T−Δ el resultado ya está casi decidido por el spot (BTC vs la apertura de la ventana). Si el mercado
tarda en reflejarlo ("Up" debería valer ~0.98 y sigue 0.85), COMPRAR el ganador ya-casi-cierto a precio
rezagado es +EV — y la Pi SÍ llega, porque es un HUECO DE VALOR que persiste, no una carrera de órdenes.

NO es la predicción muerta: el movimiento YA ocurrió y es OBSERVABLE en el spot; solo medimos si el
mercado tarda. Y OJO: esto NO es cota superior como el MM — levantar el ask como TAKER no necesita ganar
cola, la orden entra (los únicos huecos: que el ask se mueva en los ~s hasta mi orden, o que se agote el
top del libro; con el mínimo de $1 el tamaño es ínfimo).

Datos del lab: spot_*.csv (Binance, ~5s) para apertura y dirección; books_*.csv (ask del ganador-spot en
T−Δ); resolución por CLOB. EV = acierto − ask − fee_taker(0.07·p·(1−p)). Barre Δ y umbral de |movimiento|,
con corte train/test temporal para no autoengañarnos.

    cd ~/polymarket-btc-up-down/research && python3 endwindow_lag.py
"""
import csv, os, sys, glob, json, time, bisect, urllib.request

DIR = os.path.join(os.path.dirname(__file__), "lab")
DELTAS = (10, 20, 30)                 # s antes del cierre en que "compro"
MOVES = (0, 25, 50, 100)              # umbral |spot_end − spot_open| en $ (decisividad del movimiento)
TOL = 8                               # tolerancia s para casar un snapshot con un ts objetivo
TOL_SPOT = 6                          # tolerancia s del spot como-de-t (ts ≤ t, sin futuro)
CACHE = os.path.join(DIR, "clob_reso_mmtoxic.csv")   # reutiliza la caché ya poblada por los runs del MM


def get(url, tries=2):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "endwin/1.0"})
            with urllib.request.urlopen(req, timeout=12) as r: return json.load(r)
        except Exception:
            if i == tries - 1: return None
            time.sleep(0.3)


def fee(p): return 0.07 * p * (1 - p)


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


def near(series, idx, t, tol=TOL):
    """precio del reading más cercano a t dentro de tol (búsqueda binaria sobre idx)."""
    if not series: return None
    i = bisect.bisect_left(idx, t); best = None; bd = tol + 1
    for j in (i - 1, i, i + 1):
        if 0 <= j < len(series):
            d = abs(series[j][0] - t)
            if d < bd: bd = d; best = series[j][1]
    return best if bd <= tol else None


def near_le(series, idx, t, tol):
    """(ts, price) del reading MÁS RECIENTE con ts ≤ t dentro de tol (lo que un trader ve en t, SIN futuro)."""
    if not series: return None
    i = bisect.bisect_right(idx, t) - 1
    if i >= 0 and t - series[i][0] <= tol: return series[i]
    return None


def ask_ge(rows, t0, tmax):
    """(ts, ask) del PRIMER snapshot de libro con ts ≥ t0 y ≤ tmax (el precio que consigo TRAS ver el spot).
    Ancla el ask a no-antes-del-spot → mata el look-ahead de relojes desincronizados. rows ordenado por ts."""
    for ts, ask in rows:
        if ts >= t0:
            return (ts, ask) if ts <= tmax else None
    return None


def load_books_asks():
    """slug -> {cid, ws, wlen, v, asks:{Up:[(ts,ask)], Down:[(ts,ask)]}}"""
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
                    ts = int(row[0]); ask = float(row[10]) if row[10] else None
                except Exception: continue
                if ask is None: continue
                wl = 300 if v == "5m" else 900
                w = W.setdefault(slug, {"cid": row[2], "ws": int(slug.split("-")[-1]), "wlen": wl, "v": v,
                                        "asks": {"Up": [], "Down": []}})
                w["asks"][row[3]].append((ts, ask))
    return W


def main():
    spot, sidx = load_spot()
    W = load_books_asks()
    print(f"spot readings: {len(spot)} · ventanas en libro: "
          f"{sum(1 for w in W.values() if w['v']=='5m')} de 5m / {sum(1 for w in W.values() if w['v']=='15m')} de 15m")

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

    # recolectar registros: (v, D, absmove, ws, ask, hit, skew)  — ask ANCLADO a ts ≥ spot (sin look-ahead)
    print(f"\nresolviendo {len(W)} ventanas por CLOB (cacheado)…")
    recs = []; done = 0; no_open = 0; no_win = 0
    for slug, w in W.items():
        ws = w["ws"]; wlen = w["wlen"]; v = w["v"]
        open_px = near(spot, sidx, ws)
        done += 1
        if done % 1000 == 0: print(f"   … {done}/{len(W)}")
        if open_px is None: no_open += 1; continue
        win = resolve(w["cid"])
        if win not in ("Up", "Down"): no_win += 1; continue
        for s in ("Up", "Down"): w["asks"][s].sort()
        for D in DELTAS:
            t = ws + wlen - D
            sp = near_le(spot, sidx, t, TOL_SPOT)               # spot que se VE en t (ts ≤ t), sin futuro
            if sp is None: continue
            spot_ts, end_px = sp
            move = end_px - open_px
            sdir = "Up" if move > 0 else "Down"
            aq = ask_ge(w["asks"][sdir], spot_ts, ws + wlen)    # ask con ts ≥ spot_ts (el precio TRAS ver el mov.)
            if aq is None: continue
            ask_ts, ask = aq
            if not (0.0 < ask < 1.0): continue
            recs.append((v, D, abs(move), ws, ask, 1 if win == sdir else 0, ask_ts - spot_ts))
    print(f"  registros: {len(recs)} · sin apertura: {no_open} · sin ganador: {no_win}")

    def mean(xs): return sum(xs) / len(xs) if xs else 0.0
    mids = {v: sorted(r[3] for r in recs if r[0] == v)[max(0, sum(1 for r in recs if r[0] == v) // 2 - 1)]
            for v in ("5m", "15m") if any(r[0] == v for r in recs)}

    print("\n" + "=" * 94)
    print("  DESFASE FIN DE VENTANA — ask ANCLADO a ts ≥ spot (corregido look-ahead) · EV REAL taker, no cota")
    print("=" * 94)
    print("  acc% = veces que la dirección del spot en T−Δ acierta la resolución · ask = precio medio de compra")
    print("  gap = acc − ask (hueco) · EV = acc − ask − fee_taker · train/test parte por fecha (OOS)")
    print("  skew = s medios entre el ask usado y el spot (antes iba rancio; ahora ≥0 y pequeño = corregido)")
    for v in ("5m", "15m"):
        if v not in mids: continue
        for D in DELTAS:
            blk = [r for r in recs if r[0] == v and r[1] == D]
            sk = mean([r[6] for r in blk]) if blk else 0
            print(f"\n  ── {v}  ·  Δ = {D}s antes del cierre  ·  skew medio ask−spot: {sk:.1f}s " + "─" * 18)
            print(f"    {'|mov|≥$':>8}{'n':>7}{'acc%':>7}{'ask':>7}{'gap':>7}{'EV':>8}{'EV_tr':>8}{'EV_te':>8}")
            for M in MOVES:
                rows = [r for r in recs if r[0] == v and r[1] == D and r[2] >= M]
                n = len(rows)
                if not n: continue
                acc = mean([r[5] for r in rows]); ask = mean([r[4] for r in rows])
                ev = mean([r[5] - r[4] - fee(r[4]) for r in rows]) * 100
                tr = [r for r in rows if r[3] < mids[v]]; te = [r for r in rows if r[3] >= mids[v]]
                evtr = mean([r[5] - r[4] - fee(r[4]) for r in tr]) * 100
                evte = mean([r[5] - r[4] - fee(r[4]) for r in te]) * 100
                print(f"    {M:>8}{n:>7}{100*acc:>6.0f}%{ask:>7.3f}{100*(acc-ask):>+6.0f}{ev:>+8.2f}{evtr:>+8.2f}{evte:>+8.2f}")
    print("\nVEREDICTO: ahora el ask NO puede ir antes que el spot (skew ≥ 0). Si el EV se DERRUMBA vs la corrida")
    print("anterior (+2 a +30) → era look-ahead de relojes, humo. Si SOBREVIVE + en train Y test → hay desfase")
    print("real. Como es taker (levantar el ask), NO es cota superior. Compara con la corrida sin anclar.")


if __name__ == "__main__":
    try: sys.stdout.reconfigure(encoding="utf-8")
    except Exception: pass
    main()
