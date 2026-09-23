"""
turnedge.py — COMPRAR EL GIRO, no la tendencia. takeredge caracterizó por fin a los que cruzan el spread y
ganan de forma persistente (67% takers con libro fresco, z +3,2/+3,3 en las dos mitades). Frente a un control
emparejado por ventana Y por precio, cuatro rasgos repiten con el mismo signo en las dos mitades:

      margen TWAP a favor   −2,34 (z −27) / −4,60 (z −33)     ← el token AÚN NO va ganando por TWAP
      BTC 15 s a favor      +0,61 (z +6,2) / +1,36 (z +8,3)   ← pero BTC ACABA de girar hacia él
      BTC 60 s a favor      −0,93 (z −5,3) / −0,98 (z −3,5)   ← y venía yendo en su contra
      desequilibrio libro   +0,068 (z +27) / +0,057 (z +25)   ← con el libro ya inclinado a la compra

Mecanismo: el TWAP es una MEDIA, así que va con retraso por construcción. Cuando BTC gira, el margen del
TWAP todavía apunta al lado viejo; ellos compran el lado nuevo antes de que la media lo recoja. En volskew
probamos comprar el lado que el modelo decía BARATO respecto al TWAP: ellos compran el CARO, porque saben
hacia dónde va a moverse la media. Probamos la dirección contraria.

Aquí se construye esa señal y se prueba SOLA, sin mirar a ningún ganador:
  · barrido de umbrales (rejilla completa, no una celda: la lección de check15) — si solo vive en una casilla
    y sus vecinas están planas, era suerte;
  · UNA observación por ventana (primer disparo barriendo la ventana);
  · comprar al ASK fresco, aguantar a resolución, neto de comisión;
  · puertas tr/te con el MISMO corte temporal que usó persist, semanas positivas y EV sin el 1% mejor;
  · control obligatorio: la señal ESPEJO (comprar el otro token en ese mismo instante) debe perder.

    cd ~/polymarket-btc-up-down/research && python3 turnedge.py
"""
import csv, os, sys, glob, math, time, bisect
from array import array

DIR = os.path.join(os.path.dirname(__file__), "lab")
T_TWAP = 1786060800
T_5M60 = 1786665600
SCAN = [0.15 + 0.05 * i for i in range(12)]      # 15% … 70% de la ventana
D15 = (0.0, 5.0, 10.0)        # BTC a favor en los últimos 15 s, $
D60 = (99.0, 0.0, -5.0)       # BTC a favor en los últimos 60 s (99 = sin condición)
IMB = (0.0, 0.55, 0.60)       # desequilibrio del libro a favor


def fee(p): return 0.07 * p * (1 - p)
def mean(xs): return sum(xs) / len(xs) if xs else float("nan")
def week(ts): return time.strftime("%Y-%U", time.gmtime(ts))
def regime_L(v, ws):
    if ws < T_TWAP: return 60
    return 30 if (v == "5m" and ws < T_5M60) else 60


def load_spot():
    out = []
    for path in sorted(glob.glob(os.path.join(DIR, "spot_*.csv"))):
        with open(path, encoding="utf-8") as fh:
            rd = csv.reader(fh); next(rd, None)
            for row in rd:
                try: out.append((int(row[0]), float(row[1])))
                except Exception: continue
    out.sort()
    return array("i", [t for t, _ in out]), array("f", [p for _, p in out])


def load_books():
    raw = {}
    for path in sorted(glob.glob(os.path.join(DIR, "books_*.csv"))):
        with open(path, encoding="utf-8") as fh:
            rd = csv.reader(fh); next(rd, None)
            for row in rd:
                if len(row) < 12 or not row[1].startswith("btc-updown-") or row[3] not in ("Up", "Down"):
                    continue
                try:
                    ts = int(row[0]); b = float(row[4]); bs = float(row[5] or 0)
                    a = float(row[10]); asz = float(row[11] or 0)
                except Exception: continue
                if not (0 < b < a < 1): continue
                raw.setdefault(row[1], {"cid": row[2], "Up": [], "Down": []})[row[3]].append((ts, b, bs, a, asz))
    W = {}
    for slug, d in raw.items():
        if not d["Up"] or not d["Down"]: continue
        d["Up"].sort(); d["Down"].sort()
        W[slug] = d
    return W


def gates(rows):
    if len(rows) < 40: return None
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
    return (len(rows), pa, 100 * g, ev(rows),
            ev(tr) if len(tr) >= 10 else float("nan"), ev(te) if len(te) >= 10 else float("nan"),
            100 * mean([pnl(x) for x in ps[:-k]]), f"{wp}/{len(wt)}", (g - pa) / se)


HDR = (f"  {'caso':>28}{'n':>7}{'ask':>7}{'gana%':>7}{'EV':>8}{'tr':>7}{'te':>7}"
       f"{'-top1%':>8}{'sem+':>7}{'z':>7}")


def show(lab, s):
    if s is None: print(f"  {lab:>28}{'(pocos)':>9}"); return
    n, ask, g, ev, tr, te, e1, wk, z = s
    print(f"  {lab:>28}{n:>7}{ask:>7.3f}{g:>6.0f}%{ev:>+8.2f}{tr:>+7.2f}{te:>+7.2f}"
          f"{e1:>+8.2f}{wk:>7}{z:>+7.2f}")


def main():
    sts, spx = load_spot(); W = load_books()
    print(f"ventanas con libro: {len(W)} · ticks spot: {len(sts)}", flush=True)
    reso = {}
    for fn in ("clob_reso_mmtoxic.csv", "clob_reso_mmlogs.csv", "clob_reso_mw.csv",
               "clob_reso_win.csv", "clob_reso_tape.csv", "clob_reso_uni.csv"):
        p = os.path.join(DIR, fn)
        if os.path.exists(p):
            for r in csv.DictReader(open(p, encoding="utf-8")):
                if r.get("winner"): reso[r["cid"]] = r["winner"]

    def le(t, tol=12):
        i = bisect.bisect_right(sts, t) - 1
        return spx[i] if (i >= 0 and t - sts[i] <= tol) else None
    def avg(a, b):
        lo = bisect.bisect_left(sts, a); hi = bisect.bisect_right(sts, b)
        return (sum(spx[lo:hi]) / (hi - lo)) if hi - lo >= 2 else None

    # ---------- momentos evaluables, una lista por ventana ----------
    MOM = []
    for slug, d in W.items():
        cid = d["cid"]; win = reso.get(cid)
        if win not in ("Up", "Down"): continue
        v = "5m" if "-5m-" in slug else "15m"
        ws = int(slug.split("-")[-1]); wlen = 300 if v == "5m" else 900; close = ws + wlen
        L = regime_L(v, ws)
        ref = avg(ws - L, ws)
        if ref is None: continue
        seq = []
        for f in SCAN:
            t = ws + int(f * wlen)
            if t > close - 45: continue
            cur = le(t); p15 = le(t - 15); p60 = le(t - 60)
            if cur is None or p15 is None or p60 is None: continue
            row = {"ws": ws, "t": t, "won": {}, "ask": {}, "d15": {}, "d60": {}, "imb": {}, "mar": {}}
            ok = True
            for X in ("Up", "Down"):
                rows = d[X]; k = bisect.bisect_left(rows, (t, 0, 0, 0, 0))
                if k >= len(rows) or rows[k][0] > t + 15 or rows[k][0] >= close - 20: ok = False; break
                _, b, bs, a, asz = rows[k]
                if not (0 < a < 1): ok = False; break
                s = 1 if X == "Up" else -1
                row["ask"][X] = a
                row["won"][X] = 1 if win == X else 0
                row["d15"][X] = s * (cur - p15)
                row["d60"][X] = s * (cur - p60)
                row["mar"][X] = s * (cur - ref)
                row["imb"][X] = (bs / (bs + asz)) if (bs + asz) > 0 else 0.5
            if ok: seq.append(row)
        if seq: MOM.append(seq)
    print(f"ventanas evaluables: {len(MOM)} · momentos: {sum(len(s) for s in MOM)}", flush=True)
    if len(MOM) < 500: print("muestra corta"); return

    def run(a15, a60, aimb, mirror=False):
        """primer disparo por ventana; mirror = comprar el token contrario en ese mismo instante."""
        out = []
        for seq in MOM:
            hit = None
            for row in seq:
                for X in ("Up", "Down"):
                    if (row["d15"][X] >= a15 and row["d60"][X] <= a60 and row["imb"][X] >= aimb
                            and row["mar"][X] <= 0):
                        hit = (row, X); break
                if hit: break
            if not hit: continue
            row, X = hit
            Y = "Down" if X == "Up" else "Up"
            Z = Y if mirror else X
            out.append({"ws": row["ws"], "ask": row["ask"][Z], "won": row["won"][Z]})
        return out

    print("\n" + "=" * 110)
    print("  REJILLA: comprar el token con BTC girando a su favor, margen TWAP aún en contra y libro comprador")
    print("=" * 110)
    print(HDR)
    best = None
    for a15 in D15:
        for a60 in D60:
            for aimb in IMB:
                s = gates(run(a15, a60, aimb))
                lab = f"15s≥{a15:.0f} 60s≤{a60:.0f} imb≥{aimb:.2f}"
                show(lab, s)
                if s and (best is None or s[3] > best[1][3]): best = (lab, s, (a15, a60, aimb))
        print()
    if not best:
        print("ninguna combinación con muestra suficiente"); return

    print("=" * 110)
    print(f"  CONTROL ESPEJO de la mejor combinación ({best[0]}): comprar el token CONTRARIO ahí mismo")
    print("=" * 110)
    print(HDR)
    show("señal", best[1])
    show("espejo (debe perder)", gates(run(*best[2], mirror=True)))

    print("\nLECTURA: exijo (1) que la rejilla sea una MESETA y no una casilla suelta — si las vecinas están")
    print("planas, era suerte; (2) EV+ en tr Y en te con −top1% positivo y mayoría de semanas; (3) que el")
    print("espejo pierda claramente. Y ojo al 'ask': si la señal solo vive a precios medios, la comisión de")
    print("1,75pp se la come igual que a las 20 anteriores; si vive en los extremos, la comisión es barata.")


if __name__ == "__main__":
    try: sys.stdout.reconfigure(encoding="utf-8")
    except Exception: pass
    main()
