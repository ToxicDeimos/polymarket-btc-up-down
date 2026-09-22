"""
near_close.py — Zanja la anomalía recurrente del fin de ventana. En chainlink_edge (y antes en endwindow_lag)
comprar el lado que va ganando mejora al acercarse al cierre (5m T-15 +2,21pp), pero casi todo viene de la
mitad reciente (tr +0,25 / te +9,02). Sospecha: el colector espació los snapshots del libro en las últimas
semanas → el ask de "entrada" podría ser VIEJO (un precio que ya no existe) e inflar el EV.

1) CADENCIA real de snapshots del libro por semana (mediana y p90 del hueco entre snapshots consecutivos).
2) Comprar el lado líder (Binance vs apertura) a T segundos del cierre, SOLO con snapshot FRESCO (≤3s después
   de la decisión); aguantar a resolución, neto fee. EV total, tr/te y SEMANAS positivas / semanas totales.
3) CONTROL VIEJO: el mismo trade con el ask de un snapshot ≥5s ANTES de la decisión. Si viejo ≫ fresco, la
   antigüedad infla el resultado.
4) Por tamaño de la ventaja |spot − apertura|: cierres ajustados vs claros.

    cd ~/polymarket-btc-up-down/research && python3 near_close.py
"""
import csv, os, sys, glob, json, time, bisect, urllib.request

DIR = os.path.join(os.path.dirname(__file__), "lab")
CACHE = os.path.join(DIR, "clob_reso_mmtoxic.csv")
TS = {"5m": (60, 30, 20, 15, 10), "15m": (120, 60, 30, 15)}
LEADB = [("<$10", 0, 10), ("$10-30", 10, 30), ("$30-60", 30, 60), (">$60", 60, 1e9)]


def get(url, tries=2):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "nc/1.0"})
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


def load_asks():
    tmp = {}
    for path in sorted(glob.glob(os.path.join(DIR, "books_*.csv"))):
        with open(path, encoding="utf-8") as fh:
            rd = csv.reader(fh); next(rd, None)
            for row in rd:
                if len(row) < 11 or not row[1].startswith("btc-updown-") or row[3] not in ("Up", "Down"):
                    continue
                try:
                    ts = int(row[0]); ask = float(row[10]) if row[10] else None
                except Exception: continue
                if ask is None: continue
                w = tmp.setdefault(row[1], {"cid": row[2], "Up": [], "Down": []})
                w[row[3]].append((ts, ask))
    W = {}
    for slug, w in tmp.items():
        W[slug] = {"cid": w["cid"], "ws": int(slug.split("-")[-1]), "v": "5m" if "-5m-" in slug else "15m"}
        for s in ("Up", "Down"):
            r = sorted(w[s]); W[slug][s] = ([x[0] for x in r], [x[1] for x in r])
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


def known(tss, pxs, t, tol):
    i = bisect.bisect_right(tss, t) - 1
    return pxs[i] if (i >= 0 and t - tss[i] <= tol) else None


def main():
    W = load_asks(); sts, spx = load_spot()
    print(f"ventanas: {len(W)} · spot: {len(sts)}")

    # 1) cadencia por semana
    gaps = {}
    for w in W.values():
        wk = week(w["ws"])
        for s in ("Up", "Down"):
            tss = w[s][0]
            for a, b in zip(tss, tss[1:]):
                if 0 < b - a < 120: gaps.setdefault(wk, []).append(b - a)
    print("\n  1) CADENCIA de snapshots del libro por semana (segundos entre snapshots consecutivos)")
    print(f"    {'semana':>8}{'mediana':>9}{'p90':>6}{'n':>9}")
    for wk in sorted(gaps):
        g = sorted(gaps[wk]); print(f"    {wk:>8}{g[len(g)//2]:>9}{g[int(0.9*len(g))]:>6}{len(g):>9}")

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

    recs = {(v, T): [] for v in TS for T in TS[v]}   # (ws, lead$, won, ask_fresco, ask_viejo)
    done = 0
    for slug, w in W.items():
        v = w["v"]; wlen = 300 if v == "5m" else 900; ws = w["ws"]; close = ws + wlen
        b0 = known(sts, spx, ws + 2, 20)
        if b0 is None: continue
        done += 1
        if done % 4000 == 0: print(f"   … {done}")
        win = resolve(w["cid"])
        if win not in ("Up", "Down"): continue
        for T in TS[v]:
            t = close - T
            bt = known(sts, spx, t, 10)
            if bt is None or bt == b0: continue
            X = "Up" if bt > b0 else "Down"
            tss, asks = w[X]
            k = bisect.bisect_left(tss, t)
            fresh = asks[k] if (k < len(tss) and tss[k] <= t + 3 and tss[k] < close) else None
            j = bisect.bisect_right(tss, t - 5) - 1
            stale = asks[j] if (j >= 0 and t - tss[j] <= 20) else None
            if fresh is not None and not (0 < fresh < 1): fresh = None
            if stale is not None and not (0 < stale < 1): stale = None
            if fresh is None and stale is None: continue
            recs[(v, T)].append((ws, abs(bt - b0), 1 if win == X else 0, fresh, stale))

    def evs(rows, idx):
        rr = [r for r in rows if r[idx] is not None]
        return rr, 100 * mean([r[2] - r[idx] - fee(r[idx]) for r in rr]) if rr else float("nan")

    print("\n  2-3) COMPRAR EL LÍDER a T del cierre · FRESCO (ask ≤3s tras decidir) vs VIEJO (ask ≥5s antes)")
    print(f"    {'mercado·T':>10}{'n_fr':>6}{'ask_fr':>7}{'gana%':>7}{'EV_fr':>8}{'tr':>7}{'te':>7}{'sem+':>7}"
          f"{'EV_viejo':>10}{'ask_vj':>8}")
    for v in ("5m", "15m"):
        for T in TS[v]:
            rows = recs[(v, T)]
            if len(rows) < 50: continue
            mid = sorted(r[0] for r in rows)[len(rows) // 2]
            fr, e_fr = evs(rows, 3); vj, e_vj = evs(rows, 4)
            if len(fr) < 30:
                print(f"    {f'{v}·T-{T}':>10}{len(fr):>6}   (pocos frescos) · EV viejo {e_vj:+.2f}"); continue
            _, etr = evs([r for r in fr if r[0] < mid], 3); _, ete = evs([r for r in fr if r[0] >= mid], 3)
            byw = {}
            for r in fr: byw.setdefault(week(r[0]), []).append(r[2] - r[3] - fee(r[3]))
            wpos = sum(1 for x in byw.values() if len(x) >= 20 and mean(x) > 0); wtot = sum(1 for x in byw.values() if len(x) >= 20)
            print(f"    {f'{v}·T-{T}':>10}{len(fr):>6}{mean([r[3] for r in fr]):>7.3f}{100*mean([r[2] for r in fr]):>6.0f}%"
                  f"{e_fr:>+8.2f}{etr:>+7.2f}{ete:>+7.2f}{f'{wpos}/{wtot}':>7}{e_vj:>+10.2f}"
                  f"{mean([r[4] for r in vj]) if vj else float('nan'):>8.3f}")

    print("\n  4) POR TAMAÑO DE LA VENTAJA (solo entradas frescas)")
    for v in ("5m", "15m"):
        for T in TS[v]:
            rows = [r for r in recs[(v, T)] if r[3] is not None]
            if len(rows) < 100: continue
            mid = sorted(r[0] for r in rows)[len(rows) // 2]
            cells = []
            for lab, lo, hi in LEADB:
                sub = [r for r in rows if lo <= r[1] < hi]
                if len(sub) < 30: cells.append(f"{lab} (pocos)"); continue
                _, e = evs(sub, 3)
                _, etr = evs([r for r in sub if r[0] < mid], 3); _, ete = evs([r for r in sub if r[0] >= mid], 3)
                cells.append(f"{lab} n{len(sub)} {e:+.1f} ({etr:+.1f}/{ete:+.1f})")
            print(f"    {f'{v}·T-{T}':>10}: " + " · ".join(cells))

    print("\nLECTURA: si la cadencia empeora en las semanas recientes y el EV VIEJO ≫ FRESCO, la anomalía era")
    print("antigüedad del snapshot (artefacto). Si el EV FRESCO es + en tr y te y en la mayoría de semanas (sem+),")
    print("hay un edge real en los últimos segundos: el líder se paga barato. Y el desglose por ventaja dice dónde.")


if __name__ == "__main__":
    try: sys.stdout.reconfigure(encoding="utf-8")
    except Exception: pass
    main()
