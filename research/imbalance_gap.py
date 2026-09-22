"""
imbalance_gap.py — Deriva NUESTRO edge del criterio de izzyaussie (winner_criteria): sus aciertos se ordenan por
el IMBALANCE del libro (ρ +0,17, estable). Pero el imbalance a pelo ya está cotizado en el universo
(imbalance_bt): imbalance alto ⇒ precio alto ⇒ calibrado. La reconciliación: en el quintil top de imbalance los
ganadores compraban a 0,703 cuando el universo, con ese imbalance, cotiza ~0,78 → compran cuando el LIBRO va por
delante del PRECIO. Señal: gap = precio esperado para ese imbalance (y ese momento de la ventana) − ask real.
gap grande = el libro empuja a favor y el precio aún no lo refleja → comprar.

Anti-trampa: el mapa "precio esperado | imbalance, fracción" se ajusta SOLO con la mitad antigua (train) y se
aplica a ambas → la mitad reciente (test) es OOS de verdad. Entrada al ask del SIGUIENTE snapshot (≥ t+5s), no
al que disparó; aguantar a resolución, neto de fee. 1 entrada por ventana/outcome (el primer instante que
dispara). Control: gap ≤ −G (precio por encima de lo que el libro sostiene) debería salir negativo.

    cd ~/polymarket-btc-up-down/research && python3 imbalance_gap.py
"""
import csv, os, sys, glob, json, time, bisect, urllib.request

DIR = os.path.join(os.path.dirname(__file__), "lab")
TOL = 10
FRACS = (0.15, 0.25, 0.35, 0.45, 0.55, 0.65, 0.75, 0.85)
GAPS = (0.03, 0.05, 0.08, 0.12)
CACHE = os.path.join(DIR, "clob_reso_mmtoxic.csv")


def get(url, tries=2):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "igap/1.0"})
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
                except Exception: continue
                if ask is None: continue
                w = tmp.setdefault(row[1], {"cid": row[2], "Up": [], "Down": []})
                w[row[3]].append((ts, ask))
    B = {}
    for slug, w in tmp.items():
        B[slug] = {"cid": w["cid"], "ws": int(slug.split("-")[-1]), "v": "5m" if "-5m-" in slug else "15m"}
        for s in ("Up", "Down"):
            r = sorted(w[s]); B[slug][s] = ([x[0] for x in r], [x[1] for x in r])
    return B


def load_imb():
    tmp = {}
    for path in sorted(glob.glob(os.path.join(DIR, "bookdepth_*.csv"))):
        with open(path, encoding="utf-8") as fh:
            rd = csv.reader(fh); next(rd, None)
            for row in rd:
                if len(row) < 11 or not row[1].startswith("btc-updown-") or row[3] not in ("Up", "Down"):
                    continue
                try:
                    ts = int(row[0]); im = float(row[10]) if row[10] else None
                except Exception: continue
                if im is None: continue
                tmp.setdefault(row[1], {"Up": [], "Down": []})[row[3]].append((ts, im))
    S = {}
    for slug, sides in tmp.items():
        S[slug] = {}
        for s in ("Up", "Down"):
            r = sorted(sides[s]); S[slug][s] = ([x[0] for x in r], [x[1] for x in r])
    return S


def le_i(tss, t, tol=TOL):
    i = bisect.bisect_right(tss, t) - 1
    return i if (i >= 0 and t - tss[i] <= tol) else None


def first_ge(tss, t, tol=15):
    i = bisect.bisect_left(tss, t)
    return i if (i < len(tss) and tss[i] - t <= tol) else None


def fbin(fr): return 0 if fr <= 0.35 else (1 if fr <= 0.6 else 2)


def main():
    B = load_books(); IM = load_imb()
    print(f"ventanas con libro: {len(B)} · con imbalance: {len(IM)}")
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

    obs = []   # (v, ws, fr, X, imb, ask, ask_entrada, won)
    done = 0
    for slug, w in B.items():
        if slug not in IM: continue
        done += 1
        if done % 4000 == 0: print(f"   … {done}")
        win = resolve(w["cid"])
        if win not in ("Up", "Down"): continue
        wlen = 300 if w["v"] == "5m" else 900
        for fr in FRACS:
            t = w["ws"] + int(fr * wlen)
            for X in ("Up", "Down"):
                tss, asks = w[X]
                i = le_i(tss, t)
                if i is None: continue
                j = le_i(IM[slug][X][0], t)
                if j is None: continue
                k = first_ge(tss, t + 5)
                if k is None: continue
                a, ae = asks[i], asks[k]
                if not (0.0 < a < 1.0 and 0.0 < ae < 1.0): continue
                obs.append((w["v"], w["ws"], fr, X, IM[slug][X][1][j], a, ae, 1 if win == X else 0))
    print(f"observaciones: {len(obs)}")

    for v in ("5m", "15m"):
        vv = [o for o in obs if o[0] == v]
        if len(vv) < 500: continue
        mid = sorted(o[1] for o in vv)[len(vv) // 2]
        train = [o for o in vv if o[1] < mid]
        # mapa precio esperado | (decil de imbalance, tramo de ventana) — ajustado SOLO en train
        simb = sorted(o[4] for o in train)
        edges = [simb[int(q * len(simb) / 10)] for q in range(1, 10)]
        def dec(im): return bisect.bisect_right(edges, im)
        cells = {}; dcell = {}
        for o in train:
            cells.setdefault((dec(o[4]), fbin(o[2])), []).append(o[5])
            dcell.setdefault(dec(o[4]), []).append(o[5])
        def med(xs): s = sorted(xs); return s[len(s) // 2]
        emap = {k: med(x) for k, x in cells.items() if len(x) >= 30}
        dmap = {k: med(x) for k, x in dcell.items()}
        def expected(o):
            k = (dec(o[4]), fbin(o[2]))
            return emap.get(k, dmap.get(k[0]))
        rows = []
        for o in vv:
            e = expected(o)
            if e is None: continue
            rows.append((o, e - o[5]))   # gap = esperado − ask

        def ev(sub): return 100 * mean([o[7] - o[6] - fee(o[6]) for o in sub])
        tr_all = [o for o, _ in rows if o[1] < mid]; te_all = [o for o, _ in rows if o[1] >= mid]
        print("\n" + "=" * 86)
        print(f"  IMBALANCE-GAP {v} — ¿el libro va por delante del precio? · base (comprar todo): "
              f"EV {ev([o for o,_ in rows]):+.2f}pp (tr {ev(tr_all):+.2f} / te {ev(te_all):+.2f})")
        print("=" * 86)

        # quintiles de gap (todas las observaciones)
        srt = sorted(rows, key=lambda x: x[1]); m = len(srt)
        print(f"  quintiles de gap (esperado − ask):")
        print(f"    {'rango gap':>19}{'n':>7}{'ask_ent':>8}{'gana%':>7}{'EV':>8}{'EV_tr':>8}{'EV_te(OOS)':>11}")
        for q in range(5):
            sub = srt[q * m // 5:(q + 1) * m // 5]
            os_ = [o for o, _ in sub]
            tr = [o for o in os_ if o[1] < mid]; te = [o for o in os_ if o[1] >= mid]
            print(f"    {f'{sub[0][1]:+.3f} … {sub[-1][1]:+.3f}':>19}{len(sub):>7}{mean([o[6] for o in os_]):>8.3f}"
                  f"{100*mean([o[7] for o in os_]):>6.0f}%{ev(os_):>+8.2f}{ev(tr):>+8.2f}{ev(te):>+11.2f}")

        # señal: 1ª entrada por ventana/outcome con gap ≥ G (y control gap ≤ −G)
        print(f"\n  señal operable (1 entrada por ventana/outcome, la primera que dispara):")
        print(f"    {'regla':>14}{'n':>7}{'ask_ent':>8}{'gana%':>7}{'EV':>8}{'EV_tr':>8}{'EV_te(OOS)':>11}")
        for sign, lab in ((1, "gap ≥ +"), (-1, "gap ≤ −")):
            for G in GAPS:
                first = {}
                for o, g in sorted(rows, key=lambda x: x[0][2]):
                    if (sign > 0 and g >= G) or (sign < 0 and g <= -G):
                        first.setdefault((o[1], o[3]), o)
                sub = list(first.values())
                if len(sub) < 30:
                    print(f"    {lab+f'{G:.2f}':>14}{len(sub):>7}   (pocos)"); continue
                tr = [o for o in sub if o[1] < mid]; te = [o for o in sub if o[1] >= mid]
                print(f"    {lab+f'{G:.2f}':>14}{len(sub):>7}{mean([o[6] for o in sub]):>8.3f}"
                      f"{100*mean([o[7] for o in sub]):>6.0f}%{ev(sub):>+8.2f}{ev(tr):>+8.2f}{ev(te):>+11.2f}")
    print("\nLECTURA: EV_te(OOS) es el número que manda (el mapa se ajustó sin ver esa mitad). Si 'gap ≥ +G' da")
    print("EV_te positivo y crece con G, y el control 'gap ≤ −G' sale negativo → el libro adelanta al precio y")
    print("es NUESTRO edge, observable por WSS desde la Pi. Si EV_te ≈ base o −, el criterio era de su selección.")


if __name__ == "__main__":
    try: sys.stdout.reconfigure(encoding="utf-8")
    except Exception: pass
    main()
