"""
openskew.py — La referencia NO es la apertura: es la MEDIA de los L s anteriores a la apertura (regla D3,
confirmada en twap_edge). Esa media se conoce entera en el segundo 0 de la ventana. Dos consecuencias que
nadie ha medido todavía, y ninguna vive cerca del cierre (donde el libro REST del laboratorio es basura):

PARTE A — VENTAJA DE SALIDA. head = precio(apertura) − media(apertura−L, apertura). Si BTC venía subiendo,
  "Up" arranca por delante sin que se haya movido nada. ¿Gana más el lado con ventaja de salida? ¿Y el
  mercado se lo cobra, o abre en 0,50/0,50 mirando solo la apertura?

PARTE B — LÍDER VERDADERO vs LÍDER INGENUO, durante TODA la ventana. A media ventana el valor esperado del
  TWAP final es el precio actual (martingala), así que el líder real es sign(precio − referencia), no
  sign(precio − apertura), que es lo que mira quien no sabe lo del TWAP (y lo que miraban todos nuestros
  análisis viejos). Cuando DISCREPAN, si el mercado cotiza al ingenuo, ahí está el dinero: mitad de ventana,
  libro sano, sin carrera de latencia y con tiempo de poner limit.

Puertas: train/test por mediana temporal, semanas positivas y EV sin el 1% mejor (mata los espejismos de cola).

    cd ~/polymarket-btc-up-down/research && python3 openskew.py
"""
import csv, os, sys, glob, json, time, bisect, urllib.request

DIR = os.path.join(os.path.dirname(__file__), "lab")
CACHE = os.path.join(DIR, "clob_reso_mmtoxic.csv")
T_TWAP = 1786060800      # 2026-08-07 00:00 UTC
T_5M60 = 1786665600      # 2026-08-14 00:00 UTC
FRACS = {"5m": (0.10, 0.30, 0.50, 0.70), "15m": (0.10, 0.30, 0.50, 0.70, 0.85)}
HB = [("<$5", 0, 5), ("$5-15", 5, 15), ("$15-30", 15, 30), (">$30", 30, 1e9)]


def regime(v, ws):
    if ws < T_TWAP: return "puntual", 60
    if v == "5m" and ws < T_5M60: return "TWAP30", 30
    return "TWAP60", 60


def get(url, tries=2):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "oskew/1.0"})
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
def sgn(x): return 1 if x > 0 else (-1 if x < 0 else 0)
def week(ts): return time.strftime("%Y-%U", time.gmtime(ts))


def load_spot():
    out = []
    for path in sorted(glob.glob(os.path.join(DIR, "spot_*.csv"))):
        with open(path, encoding="utf-8") as fh:
            rd = csv.reader(fh); next(rd, None)
            for row in rd:
                try: out.append((int(row[0]), float(row[1])))
                except Exception: continue
    out.sort(); return [t for t, _ in out], [p for _, p in out]


def load_books():
    """ts, slug, cid, side, b1(4) ... a1(10). Guardamos bid y ask por lado."""
    tmp = {}
    for path in sorted(glob.glob(os.path.join(DIR, "books_*.csv"))):
        with open(path, encoding="utf-8") as fh:
            rd = csv.reader(fh); next(rd, None)
            for row in rd:
                if len(row) < 11 or not row[1].startswith("btc-updown-") or row[3] not in ("Up", "Down"):
                    continue
                try:
                    ts = int(row[0])
                    bid = float(row[4]) if row[4] else None
                    ask = float(row[10]) if row[10] else None
                except Exception: continue
                if ask is None: continue
                w = tmp.setdefault(row[1], {"cid": row[2], "Up": [], "Down": []})
                w[row[3]].append((ts, ask, bid))
    W = {}
    for slug, w in tmp.items():
        if not w["Up"] or not w["Down"]: continue
        W[slug] = {"cid": w["cid"], "ws": int(slug.split("-")[-1]), "v": "5m" if "-5m-" in slug else "15m"}
        for s in ("Up", "Down"):
            r = sorted(w[s])
            W[slug][s] = ([x[0] for x in r], [x[1] for x in r], [x[2] for x in r])
    return W


def stats(rows, sk, ak, mid, minn=30):
    rr = [r for r in rows if r[ak] is not None]
    if len(rr) < minn: return None

    def won(r): return 1 if ((r[sk] == "Up") == (r["w"] > 0)) else 0
    def pnl(r): return won(r) - r[ak] - fee(r[ak])
    def ev(s): return 100 * mean([pnl(x) for x in s])

    tr = [r for r in rr if r["ws"] < mid]; te = [r for r in rr if r["ws"] >= mid]
    ps = sorted(rr, key=pnl); k = max(1, int(len(rr) * 0.01))
    ev1 = 100 * mean([pnl(x) for x in ps[:-k]])
    byw = {}
    for r in rr: byw.setdefault(week(r["ws"]), []).append(r)
    wt = [s for s in byw.values() if len(s) >= 10]
    wp = sum(1 for s in wt if ev(s) > 0)
    return (len(rr), mean([r[ak] for r in rr]), 100 * mean([won(r) for r in rr]), ev(rr),
            ev(tr) if len(tr) >= 10 else float("nan"), ev(te) if len(te) >= 10 else float("nan"),
            ev1, f"{wp}/{len(wt)}")


HDR = f"  {'caso':>26}{'n':>6}{'ask':>7}{'gana%':>7}{'EV':>8}{'tr':>7}{'te':>7}{'-top1%':>8}{'sem+':>7}"


def show(lab, s):
    if s is None: print(f"  {lab:>26}{'(pocos)':>8}"); return
    n, ask, g, ev, tr, te, ev1, wk = s
    print(f"  {lab:>26}{n:>6}{ask:>7.3f}{g:>6.0f}%{ev:>+8.2f}{tr:>+7.2f}{te:>+7.2f}{ev1:>+8.2f}{wk:>7}")


def main():
    sts, spx = load_spot(); W = load_books()
    print(f"ventanas con libro: {len(W)} · ticks spot: {len(sts)}")

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

    # ---------- preparación: por ventana, referencia TWAP y apertura ----------
    base = {}; moves = {"5m": [], "15m": []}
    done = 0
    for slug, w in W.items():
        v = w["v"]; ws = w["ws"]; wlen = 300 if v == "5m" else 900
        reg, L = regime(v, ws)
        if reg == "puntual": continue
        p0 = le(ws + 2, 8); ref = avg(ws - L, ws)
        if p0 is None or ref is None: continue
        done += 1
        if done % 3000 == 0: print(f"   … {done}")
        win = resolve(w["cid"])
        if win not in ("Up", "Down"): continue
        pc = le(ws + wlen, 8)
        if pc is not None: moves[v].append(abs(pc - p0))
        base[slug] = (p0, ref, L, 1 if win == "Up" else -1)

    sig = {v: med(m) for v, m in moves.items()}
    print(f"movimiento típico de la ventana (|cierre−apertura| mediano): "
          + " · ".join(f"{v} ${sig[v]:.0f}" for v in ("5m", "15m") if moves[v]))

    def fresh(w, X, t, close, tol=15):
        """primer libro con ts ≥ t (decidimos con spot ≤ t, compramos después): sin look-ahead."""
        tss, asks, bids = w[X]; k = bisect.bisect_left(tss, t)
        if k < len(tss) and tss[k] <= t + tol and tss[k] < close - 20:
            a = asks[k]; b = bids[k]
            return (a if 0 < a < 1 else None, b if (b is not None and 0 < b < 1) else None)
        return (None, None)

    # ================= PARTE A — VENTAJA DE SALIDA =================
    A = {}
    for slug, w in W.items():
        if slug not in base: continue
        v = w["v"]; ws = w["ws"]; wlen = 300 if v == "5m" else 900; close = ws + wlen
        p0, ref, L, wsg = base[slug]
        head = p0 - ref
        if sgn(head) == 0: continue
        X = "Up" if head > 0 else "Down"; Y = "Down" if head > 0 else "Up"
        aX, bX = fresh(w, X, ws + 5, close); aY, _ = fresh(w, Y, ws + 5, close)
        A.setdefault((v, regime(v, ws)[0]), []).append(
            {"ws": ws, "h": abs(head), "X": X, "Y": Y, "aX": aX, "aY": aY, "bX": bX, "w": wsg})

    print("\n" + "=" * 96)
    print("  PARTE A — VENTAJA DE SALIDA: comprar al que arranca por delante de la media previa (a T+5s)")
    print("=" * 96)
    for key in sorted(A):
        rows = A[key]
        if len(rows) < 60: continue
        mid = sorted(r["ws"] for r in rows)[len(rows) // 2]
        print(f"\n  {key[0]}·{key[1]}  (n ventanas {len(rows)})")
        print(HDR)
        show("con ventaja (todas)", stats(rows, "X", "aX", mid))
        show("sin ventaja (control)", stats(rows, "Y", "aY", mid))
        for nm, lo, hi in HB:
            sub = [r for r in rows if lo <= r["h"] < hi]
            show(f"con ventaja {nm}", stats(sub, "X", "aX", mid))
        show("con ventaja, al BID (maker)", stats([r for r in rows if r["bX"] is not None], "X", "bX", mid))

    # ================= PARTE B — LÍDER VERDADERO vs INGENUO =================
    B = {}
    for slug, w in W.items():
        if slug not in base: continue
        v = w["v"]; ws = w["ws"]; wlen = 300 if v == "5m" else 900; close = ws + wlen
        p0, ref, L, wsg = base[slug]
        for f in FRACS[v]:
            t = ws + int(f * wlen)
            cur = le(t, 12)
            if cur is None: continue
            mr = cur - ref; mn = cur - p0
            if sgn(mr) == 0 or sgn(mn) == 0: continue
            R = "Up" if mr > 0 else "Down"; N = "Up" if mn > 0 else "Down"
            aR, bR = fresh(w, R, t, close); aN, _ = fresh(w, N, t, close)
            B.setdefault((v, regime(v, ws)[0], f), []).append(
                {"ws": ws, "m": abs(mr), "R": R, "N": N, "aR": aR, "aN": aN, "bR": bR, "w": wsg})

    print("\n" + "=" * 96)
    print("  PARTE B — LÍDER REAL (precio vs MEDIA previa) frente a LÍDER INGENUO (precio vs APERTURA)")
    print("=" * 96)
    for key in sorted(B):
        rows = B[key]
        if len(rows) < 80: continue
        mid = sorted(r["ws"] for r in rows)[len(rows) // 2]
        dis = [r for r in rows if r["R"] != r["N"]]
        print(f"\n  {key[0]}·{key[1]}  t = apertura + {int(key[2]*100)}% de la ventana   "
              f"(n {len(rows)} · discrepan {len(dis)} = {100*len(dis)/len(rows):.0f}%)")
        print(HDR)
        show("líder real (todas)", stats(rows, "R", "aR", mid))
        show("DISCREPAN: líder real", stats(dis, "R", "aR", mid))
        show("DISCREPAN: líder ingenuo", stats(dis, "N", "aN", mid))
        show("DISCREPAN: real, al BID", stats([r for r in dis if r["bR"] is not None], "R", "bR", mid))
        for nm, lo, hi in HB:
            sub = [r for r in dis if lo <= r["m"] < hi]
            show(f"DISCREPAN real {nm}", stats(sub, "R", "aR", mid))

    print("\nLECTURA: en A, si 'con ventaja' gana >50% pero el ask sale ~0,50 → el mercado abre sin mirar la media")
    print("previa y la ventaja de salida es gratis. En B, la fila que decide es 'DISCREPAN: líder real': el mercado")
    print("cotiza al ingenuo y nosotros compramos al que de verdad va ganando. Exijo EV+ en tr Y en te, que 'líder")
    print("ingenuo' salga negativo (simetría), y que -top1% siga positivo. Si solo aguanta al BID → hay que ser")
    print("maker y toca medir el relleno, no el EV teórico.")


if __name__ == "__main__":
    try: sys.stdout.reconfigure(encoding="utf-8")
    except Exception: pass
    main()
