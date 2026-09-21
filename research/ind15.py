"""
ind15.py — ¿Usan los ganadores un INDICADOR clásico que SÍ funciona en el 15m? Los ~13 indicadores los
probamos en 5m (eficiente), pero el 15m es más lento/menos líquido y la Pi tiene minutos para actuar. Aquí,
sobre el SPOT de BTC (Binance) dentro de cada ventana de 15m, a un instante de decisión (con tiempo de sobra
antes del cierre), calculo indicadores clásicos SIN look-ahead y compro la dirección que marcan al ask,
aguantando a resolución. EV = gana − ask − fee: el test JUSTO es si el indicador BATE al precio del mercado
(o el mercado ya lo cotiza y sale −). Con train/test. Si alguno da +EV estable → el usuario tiene razón.

Indicadores en el instante t: MOM_open (spot vs open) · MA_cross (SMA60 vs SMA300) · PX_vs_MA (spot vs SMA300)
· ROC (spot vs spot−120s). Referencia: FAVORITO (el pick del propio mercado). Decisión a ws+600 y ws+780.

    cd ~/polymarket-btc-up-down/research && python3 ind15.py
"""
import csv, os, sys, glob, json, time, bisect, urllib.request

DIR = os.path.join(os.path.dirname(__file__), "lab")
TOL = 12; DECS = (600, 780)
CACHE = os.path.join(DIR, "clob_reso_mmtoxic.csv")


def get(url, tries=2):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "ind/1.0"})
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


def load_asks15():
    W = {}
    for path in sorted(glob.glob(os.path.join(DIR, "books_*.csv"))):
        with open(path, encoding="utf-8") as fh:
            rd = csv.reader(fh); next(rd, None)
            for row in rd:
                if len(row) < 11 or not row[1].startswith("btc-updown-15m-") or row[3] not in ("Up", "Down"):
                    continue
                try:
                    ts = int(row[0]); ask = float(row[10]) if row[10] else None
                except Exception: continue
                if ask is None: continue
                w = W.setdefault(row[1], {"cid": row[2], "ws": int(row[1].split("-")[-1]), "Up": [], "Down": []})
                w[row[3]].append((ts, ask))
    for w in W.values():
        for s in ("Up", "Down"): w[s].sort()
    return W


def load_spot():
    out = []
    for path in sorted(glob.glob(os.path.join(DIR, "spot_*.csv"))):
        with open(path, encoding="utf-8") as fh:
            rd = csv.reader(fh); next(rd, None)
            for row in rd:
                try: out.append((int(row[0]), float(row[1])))
                except Exception: continue
    out.sort(); return [t for t, _ in out], [p for _, p in out]


def le_ask(rows, t, tol=TOL):
    ts = [r[0] for r in rows]; i = bisect.bisect_right(ts, t) - 1
    return rows[i][1] if (i >= 0 and t - rows[i][0] <= tol) else None


def spot_at(stss, spx, t, tol=30):
    i = bisect.bisect_right(stss, t) - 1
    return spx[i] if (i >= 0 and t - stss[i] <= tol) else None


def sma(stss, spx, t, N):
    lo = bisect.bisect_left(stss, t - N); hi = bisect.bisect_right(stss, t)
    if hi - lo < 2: return None
    seg = spx[lo:hi]; return sum(seg) / len(seg)


def main():
    W = load_asks15(); stss, spx = load_spot()
    print(f"ventanas 15m: {len(W)} · spot: {len(stss)}")
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

    def mean(xs): return sum(xs) / len(xs) if xs else float("nan")

    for DEC in DECS:
        # data[ind] = list (ws, ask, hit)
        data = {k: [] for k in ("MOM_open", "MA_cross", "PX_vs_MA", "ROC", "FAVORITO")}
        done = 0
        for slug, w in W.items():
            win = resolve(w["cid"]); done += 1
            if done % 3000 == 0: print(f"   … {done}/{len(W)} (dec {DEC})")
            if win not in ("Up", "Down"): continue
            ws = w["ws"]; t = ws + DEC
            op = spot_at(stss, spx, ws); cur = spot_at(stss, spx, t)
            s60 = sma(stss, spx, t, 60); s300 = sma(stss, spx, t, 300); roc = spot_at(stss, spx, t - 120)
            au = le_ask(w["Up"], t); ad = le_ask(w["Down"], t)
            if None in (op, cur, au, ad): continue
            calls = {}
            calls["MOM_open"] = "Up" if cur > op else "Down"
            if s60 is not None and s300 is not None: calls["MA_cross"] = "Up" if s60 > s300 else "Down"
            if s300 is not None: calls["PX_vs_MA"] = "Up" if cur > s300 else "Down"
            if roc is not None: calls["ROC"] = "Up" if cur > roc else "Down"
            calls["FAVORITO"] = "Up" if au >= ad else "Down"
            for k, d in calls.items():
                ask = au if d == "Up" else ad
                if 0.0 < ask < 1.0: data[k].append((ws, ask, 1 if win == d else 0))
        print("\n" + "=" * 78)
        print(f"  INDICADORES 15m a ws+{DEC}s ({(900-DEC)//60}min antes del cierre) · comprar la dirección, aguantar")
        print("=" * 78)
        print(f"  {'indicador':>10}{'n':>7}{'ask':>7}{'gana%':>7}{'EV':>8}{'EV_tr':>8}{'EV_te':>8}")
        for k in ("MOM_open", "MA_cross", "PX_vs_MA", "ROC", "FAVORITO"):
            rows = data[k]; n = len(rows)
            if n < 50: continue
            m = sorted(r[0] for r in rows)[n // 2]
            ask = mean([r[1] for r in rows]); wr = 100 * mean([r[2] for r in rows])
            ev = 100 * mean([r[2] - r[1] - fee(r[1]) for r in rows])
            tr = [r for r in rows if r[0] < m]; te = [r for r in rows if r[0] >= m]
            evtr = 100 * mean([r[2] - r[1] - fee(r[1]) for r in tr]) if tr else float("nan")
            evte = 100 * mean([r[2] - r[1] - fee(r[1]) for r in te]) if te else float("nan")
            print(f"  {k:>10}{n:>7}{ask:>7.3f}{wr:>6.0f}%{ev:>+7.2f}{evtr:>+8.2f}{evte:>+8.2f}")
    print("\nLECTURA: si algún indicador da EV + y estable (tr y te) → BATE al precio → señal predictiva real en")
    print("15m (el usuario tiene razón), computable y accionable por la Pi. Si todos ≈ el FAVORITO (−, pagas el")
    print("spread) → el mercado ya cotiza el indicador y también el 15m es eficiente al taker.")


if __name__ == "__main__":
    try: sys.stdout.reconfigure(encoding="utf-8")
    except Exception: pass
    main()
