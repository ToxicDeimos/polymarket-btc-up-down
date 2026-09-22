"""
tape_check.py — ¿Es EJECUTABLE el ask barato del sniper? sniper_bt dio EV enorme comprando el líder con ventaja
grande en los últimos segundos, PERO el ask 6s después sale aún MÁS barato (EV_sig > EV_visto) — físicamente
raro: con BTC ganando por $60+ y segundos para el cierre el precio del líder debería subir hacia 1, y si hubiera
snipers se llevarían esos asks. Dos lecturas opuestas:
  A) REAL: alguien vende al ganador barato al final y se puede comprar.
  B) ARTEFACTO: el libro registrado al final no es ejecutable (API cacheada en la transición de ventanas, o el
     mercado deja de casar antes del cierre).
La CINTA (wintrades: operaciones reales) decide. Para cada disparo del sniper, sobre el lado líder entre el
disparo y el cierre: ¿hubo operaciones?, ¿a qué precio?, ¿alguna al precio barato del libro? Y el
last_trade_price que devuelve el propio endpoint del libro vs su ask. Más: actividad de la cinta por tramo
de los últimos 60s (¿se para la negociación?).

    cd ~/polymarket-btc-up-down/research && python3 tape_check.py
"""
import csv, os, sys, glob, json, time, bisect, urllib.request

DIR = os.path.join(os.path.dirname(__file__), "lab")
CACHE = os.path.join(DIR, "clob_reso_mmtoxic.csv")
CFGS = [("5m", 60, 30), ("5m", 60, 10), ("5m", 30, 20), ("15m", 60, 30), ("15m", 60, 15)]


def get(url, tries=2):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "tape/1.0"})
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


def med(xs):
    s = sorted(xs); return s[len(s) // 2] if s else float("nan")
def mean(xs): return sum(xs) / len(xs) if xs else float("nan")


def load_books():
    tmp = {}
    for path in sorted(glob.glob(os.path.join(DIR, "books_*.csv"))):
        with open(path, encoding="utf-8") as fh:
            rd = csv.reader(fh); next(rd, None)
            for row in rd:
                if len(row) < 11 or not row[1].startswith("btc-updown-") or row[3] not in ("Up", "Down"):
                    continue
                try:
                    ts = int(row[0]); ask = float(row[10]) if row[10] else None
                    last = float(row[16]) if len(row) > 16 and row[16] else None
                except Exception: continue
                if ask is None: continue
                w = tmp.setdefault(row[1], {"cid": row[2], "Up": [], "Down": []})
                w[row[3]].append((ts, ask, last))
    W = {}
    for slug, w in tmp.items():
        W[slug] = {"cid": w["cid"], "ws": int(slug.split("-")[-1]), "v": "5m" if "-5m-" in slug else "15m"}
        for s in ("Up", "Down"):
            r = sorted(w[s]); W[slug][s] = ([x[0] for x in r], [x[1] for x in r], [x[2] for x in r])
    return W


def load_trades(cids):
    T = {}; seen = set()
    for path in sorted(glob.glob(os.path.join(DIR, "wintrades_*.csv"))):
        with open(path, encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                cid = r.get("cid")
                if cid not in cids or r.get("outcome") not in ("Up", "Down"): continue
                k = (r.get("tx", ""), r["outcome"], r.get("price"), r.get("trade_side"))
                if k in seen: continue
                seen.add(k)
                try: ts = int(float(r["ts_trade"])); pr = float(r["price"])
                except Exception: continue
                T.setdefault(cid, []).append((ts, r["outcome"], r.get("trade_side"), pr))
    for cid in T: T[cid].sort()
    return T


def load_spot():
    out = []
    for path in sorted(glob.glob(os.path.join(DIR, "spot_*.csv"))):
        with open(path, encoding="utf-8") as fh:
            rd = csv.reader(fh); next(rd, None)
            for row in rd:
                try: out.append((int(row[0]), float(row[1])))
                except Exception: continue
    out.sort(); return [t for t, _ in out], [p for _, p in out]


def known_strict(tss, pxs, t, tol):
    i = bisect.bisect_left(tss, t) - 1
    return pxs[i] if (i >= 0 and t - tss[i] <= tol) else None


def main():
    W = load_books(); sts, spx = load_spot()
    TR = load_trades(set(w["cid"] for w in W.values()))
    print(f"ventanas: {len(W)} · con cinta: {len(TR)}")
    reso = {}
    for fn in ("clob_reso_mmtoxic.csv", "clob_reso_mmlogs.csv", "clob_reso_mw.csv",
               "clob_reso_win.csv", "clob_reso_tape.csv", "clob_reso_uni.csv"):
        p = os.path.join(DIR, fn)
        if os.path.exists(p):
            for r in csv.DictReader(open(p, encoding="utf-8")):
                if r["winner"]: reso[r["cid"]] = r["winner"]

    # actividad de la cinta por tramo final
    print("\n  ACTIVIDAD DE LA CINTA en el final de ventana (operaciones por ventana, media)")
    for v, wlen in (("5m", 300), ("15m", 900)):
        buckets = [(60, 30), (30, 20), (20, 10), (10, 0), (0, -30)]
        cnt = {b: 0 for b in buckets}; nwin = 0
        for w in W.values():
            if w["v"] != v or w["cid"] not in TR: continue
            nwin += 1; close = w["ws"] + wlen
            for ts, *_ in TR[w["cid"]]:
                d = close - ts
                for b in buckets:
                    if b[1] < d <= b[0]: cnt[b] += 1
        if nwin:
            print(f"    {v}: " + " · ".join(f"[T-{b[0]}..T-{b[1]}] {cnt[b]/nwin:.2f}" if b[1] >= 0 else
                                         f"[tras cierre 0..30s] {cnt[b]/nwin:.2f}" for b in buckets) + f"  (n={nwin})")

    print("\n  DISPAROS DEL SNIPER vs CINTA REAL del lado líder (del disparo al cierre)")
    print(f"  {'cfg':>12}{'n':>6}{'gana%':>7}{'ask_libro':>10}{'last_libro':>11}{'%con_trades':>12}{'precio_cinta':>13}"
          f"{'%trade≤ask+2¢':>14}{'%trade≥0,90':>12}")
    for v, L, T in CFGS:
        wlen = 300 if v == "5m" else 900
        rows = []
        for w in W.values():
            if w["v"] != v or w["cid"] not in TR: continue
            ws = w["ws"]; close = ws + wlen
            b0 = known_strict(sts, spx, ws + 3, 20)
            if b0 is None: continue
            hit = None
            for X in ("Up", "Down"):
                tss, asks, lasts = w[X]
                lo = bisect.bisect_left(tss, close - T)
                for k in range(lo, len(tss)):
                    ts = tss[k]
                    if ts > close - 2: break
                    s = known_strict(sts, spx, ts, 12)
                    if s is None: continue
                    lead = s - b0
                    if ("Up" if lead > 0 else "Down") != X or abs(lead) < L or not (0 < asks[k] < 1): continue
                    cand = (ts, X, asks[k], lasts[k])
                    if hit is None or ts < hit[0]: hit = cand
                    break
            if hit is None: continue
            ts, X, a, last = hit
            win = reso.get(w["cid"])
            prices = [pr for t2, oc, sd, pr in TR[w["cid"]] if oc == X and ts <= t2 <= close + 2]
            rows.append((a, last, prices, (1 if win == X else 0) if win in ("Up", "Down") else None))
        if not rows:
            print(f"  {f'{v} L{L} T{T}':>12}     0"); continue
        withp = [r for r in rows if r[2]]
        cheap = 100 * mean([1 if any(p <= r[0] + 0.02 for p in r[2]) else 0 for r in withp]) if withp else float("nan")
        high = 100 * mean([1 if any(p >= 0.90 for p in r[2]) else 0 for r in withp]) if withp else float("nan")
        won = [r[3] for r in rows if r[3] is not None]
        lasts = [r[1] for r in rows if r[1] is not None]
        print(f"  {f'{v} L{L} T{T}':>12}{len(rows):>6}{100*mean(won):>6.0f}%{med([r[0] for r in rows]):>10.3f}"
              f"{med(lasts):>11.3f}{100*len(withp)/len(rows):>11.0f}%{med([med(r[2]) for r in withp]):>13.3f}"
              f"{cheap:>13.0f}%{high:>11.0f}%")
    print("\nLECTURA: si tras el disparo SÍ se opera y buena parte de las operaciones son ≤ el ask del libro (+2¢)")
    print("→ el ask barato era real y ejecutable (A). Si la cinta opera a ≥0,90 mientras el libro marca ~0,6, o")
    print("last_libro ≫ ask_libro → el libro registrado al final está viejo/cacheado (B, artefacto). Si casi no hay")
    print("operaciones en los últimos segundos → se para la negociación y no se puede ejecutar (B).")


if __name__ == "__main__":
    try: sys.stdout.reconfigure(encoding="utf-8")
    except Exception: pass
    main()
