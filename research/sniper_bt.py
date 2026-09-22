"""
sniper_bt.py — Valida como REGLA OPERABLE el hallazgo de near_close: en los últimos segundos, cuando BTC lleva una
ventaja GRANDE sobre la apertura, el ask del lado líder se queda barato (makers lentos en actualizar/retirar) y
un taker lo compra con EV muy positivo (>$60: +11 a +25pp, + en train y test). Estrategia de TAKER: no hace falta
ganar la cola; la Pi puede ejecutarla si el ask barato AGUANTA un momento.

Regla: en cada ventana, en cada snapshot del libro dentro de los últimos Tmax segundos, lead = spot(conocido en
ese instante, del loop anterior) − apertura. Si |lead| ≥ L → comprar el líder. 1 disparo por ventana (el primero).
Mide: EV con el ask VISTO en ese snapshot (lo levantas al verlo) y con el ask del SNAPSHOT SIGUIENTE (~6s después,
pesimista); persistencia (¿sigue barato 6s después?); liquidez en el mejor ask ($ = tamaño × precio); train/test y
semanas positivas. Aguantar a resolución, neto de fee taker.

    cd ~/polymarket-btc-up-down/research && python3 sniper_bt.py
"""
import csv, os, sys, glob, json, time, bisect, urllib.request

DIR = os.path.join(os.path.dirname(__file__), "lab")
CACHE = os.path.join(DIR, "clob_reso_mmtoxic.csv")
TMAX = {"5m": (30, 20, 10), "15m": (60, 30, 15)}
LEADS = (30, 60, 100)


def get(url, tries=2):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "snp/1.0"})
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
def week(ts): return time.strftime("%Y-%U", time.gmtime(ts))


def load_books():
    tmp = {}
    for path in sorted(glob.glob(os.path.join(DIR, "books_*.csv"))):
        with open(path, encoding="utf-8") as fh:
            rd = csv.reader(fh); next(rd, None)
            for row in rd:
                if len(row) < 12 or not row[1].startswith("btc-updown-") or row[3] not in ("Up", "Down"):
                    continue
                try:
                    ts = int(row[0]); ask = float(row[10]) if row[10] else None
                    asz = float(row[11]) if row[11] else 0.0
                except Exception: continue
                if ask is None: continue
                w = tmp.setdefault(row[1], {"cid": row[2], "Up": [], "Down": []})
                w[row[3]].append((ts, ask, asz))
    W = {}
    for slug, w in tmp.items():
        W[slug] = {"cid": w["cid"], "ws": int(slug.split("-")[-1]), "v": "5m" if "-5m-" in slug else "15m"}
        for s in ("Up", "Down"):
            r = sorted(w[s]); W[slug][s] = ([x[0] for x in r], [x[1] for x in r], [x[2] for x in r])
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


def known_strict(tss, pxs, t, tol):
    """último spot con ts ESTRICTAMENTE anterior a t (el del loop anterior: conservador)."""
    i = bisect.bisect_left(tss, t) - 1
    return pxs[i] if (i >= 0 and t - tss[i] <= tol) else None


def main():
    W = load_books(); sts, spx = load_spot()
    print(f"ventanas: {len(W)} · spot: {len(sts)}")
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
            with open(CACHE, "a", newline="", encoding="utf-8") as fo:
                cw = csv.writer(fo)
                if nf: cw.writerow(["cid", "winner"])
                cw.writerow([cid, w])
        return w

    R = {(v, L, T): [] for v in TMAX for L in LEADS for T in TMAX[v]}
    done = 0
    for slug, w in W.items():
        v = w["v"]; wlen = 300 if v == "5m" else 900; ws = w["ws"]; close = ws + wlen
        b0 = known_strict(sts, spx, ws + 3, 20)
        if b0 is None: continue
        done += 1
        if done % 4000 == 0: print(f"   … {done}")
        win = resolve(w["cid"])
        if win not in ("Up", "Down"): continue
        for T in TMAX[v]:
            for L in LEADS:
                hit = None
                for X in ("Up", "Down"):
                    tss, asks, sz = w[X]
                    lo = bisect.bisect_left(tss, close - T)
                    for k in range(lo, len(tss)):
                        ts = tss[k]
                        if ts > close - 2: break
                        s = known_strict(sts, spx, ts, 12)
                        if s is None: continue
                        lead = s - b0
                        lx = "Up" if lead > 0 else "Down"
                        if lx != X or abs(lead) < L: continue
                        if not (0 < asks[k] < 1): continue
                        nxt = asks[k + 1] if (k + 1 < len(tss) and tss[k + 1] < close) else None
                        cand = (ts, X, asks[k], sz[k], nxt)
                        if hit is None or cand[0] < hit[0]: hit = cand
                        break
                if hit is None: continue
                ts, X, a, asz, nxt = hit
                R[(v, L, T)].append((ws, 1 if win == X else 0, a, asz, nxt if (nxt is not None and 0 < nxt < 1) else None))

    print("\n" + "=" * 110)
    print("  SNIPER: comprar el líder cuando |spot − apertura| ≥ L con ≤ T s para el cierre (1 por ventana)")
    print("=" * 110)
    print(f"  {'mercado':>8}{'L$':>5}{'T':>4}{'n':>6}{'ask':>7}{'gana%':>7}{'EV_visto':>10}{'tr':>7}{'te':>7}{'sem+':>7}"
          f"{'EV_sig':>8}{'persist':>9}{'liq$med':>9}")
    for v in ("5m", "15m"):
        for T in TMAX[v]:
            for L in LEADS:
                rows = R[(v, L, T)]
                if len(rows) < 30:
                    print(f"  {v:>8}{L:>5}{T:>4}{len(rows):>6}   (pocos)"); continue
                mid = sorted(r[0] for r in rows)[len(rows) // 2]
                def ev(s, i=2): return 100 * mean([r[1] - r[i] - fee(r[i]) for r in s if r[i] is not None])
                tr = [r for r in rows if r[0] < mid]; te = [r for r in rows if r[0] >= mid]
                byw = {}
                for r in rows: byw.setdefault(week(r[0]), []).append(r[1] - r[2] - fee(r[2]))
                wpos = sum(1 for x in byw.values() if len(x) >= 10 and mean(x) > 0)
                wtot = sum(1 for x in byw.values() if len(x) >= 10)
                withn = [r for r in rows if r[4] is not None]
                pers = 100 * mean([1 if r[4] <= r[2] + 0.02 else 0 for r in withn]) if withn else float("nan")
                liq = sorted(r[3] * r[2] for r in rows)[len(rows) // 2]
                print(f"  {v:>8}{L:>5}{T:>4}{len(rows):>6}{mean([r[2] for r in rows]):>7.3f}{100*mean([r[1] for r in rows]):>6.0f}%"
                      f"{ev(rows):>+10.2f}{ev(tr):>+7.2f}{ev(te):>+7.2f}{f'{wpos}/{wtot}':>7}{ev(withn, 4):>+8.2f}"
                      f"{pers:>8.0f}%{liq:>9.1f}")
    print("\nLECTURA: EV_visto = levantas el ask que viste; EV_sig = ejecutas 6s tarde (pesimista). 'persist' = % de")
    print("veces que el ask sigue ≤ visto+2¢ en el snapshot siguiente. 'liq$med' = $ disponibles en el mejor ask.")
    print("Si EV_visto y EV_sig son + en tr y te, con muchas semanas+ y liquidez ≥ $1 → edge de sniper ejecutable")
    print("por la Pi. Si solo vive en EV_visto y se muere en EV_sig → exige velocidad (que el ask no dure).")


if __name__ == "__main__":
    try: sys.stdout.reconfigure(encoding="utf-8")
    except Exception: pass
    main()
