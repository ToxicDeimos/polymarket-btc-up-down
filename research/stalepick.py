"""
stalepick.py — QUITARLE LA COTIZACIÓN RANCIA AL MAKER. wallet1 destapó el mecanismo: la wallet grande
(0x0c7c52…, 9.366 operaciones, z +3,26 y +3,20 en dos mitades independientes) SOLO COMPRA, aguanta a
resolución, y bate el precio que paga en +2,6 a +3,6pp **en las SIETE franjas de precio**:

      <0,15  paga  8,3 gana 11,0   ·  0,45-0,55 paga 50,3 gana 53,6  ·  ≥0,85 paga 91,0 gana 94,1

Una ventaja PLANA en todo el rango no es una señal direccional (esas se concentran en zonas): es una
propiedad de la ORDEN que compran, no del lado que eligen. Y encaja con markout, que midió que una cotización
rancia le cuesta al maker ~1pp por segundo de retraso — esa pérdida del maker es la ganancia del que se la
quita. 3pp ≈ 3 s de retraso, justo lo que tarda el libro en repreciar. También encaja con takeredge: compran
tras un movimiento de BTC de 15 s a su favor con el margen TWAP aún apuntando al lado viejo, que es la
descripción literal de un libro que no ha repreciado.

Y esta carrera SÍ es nuestra liga: la cola exige <10 ms porque compites con quien ya está puesto; quitar una
cotización rancia da SEGUNDOS, porque compites con el maker que tiene que enterarse y recotizar.

CÓMO SE MIDE SIN INVENTARSE UNA σ: se construye la curva de precios DEL PROPIO MERCADO — la mediana del medio
observado para cada nivel de margen/√τ — AJUSTADA SOLO EN LA PRIMERA MITAD del tiempo. Luego, en cada
instante, "rancio" = cuánto está el ask por DEBAJO de lo que esa curva dice para el margen de AHORA. Sin
modelo de volatilidad, sin parámetros libres: la referencia es el mercado consigo mismo.

Filtros que exige el mecanismo (y que a volskew le faltaron):
  · spread ESTRECHO — el libro ancho da un medio sin sentido y fue lo que envenenó volskew;
  · movimiento RECIENTE del spot — el retraso solo existe si acaba de pasar algo que repreciar.
Puertas: rejilla completa de umbrales, una observación por ventana, ask fresco, neto de comisión, tr/te
(la 2ª mitad NO participa en el ajuste de la curva), semanas positivas, sin el 1% mejor y control espejo.

    cd ~/polymarket-btc-up-down/research && python3 stalepick.py
"""
import csv, os, sys, glob, math, time, bisect
from array import array

DIR = os.path.join(os.path.dirname(__file__), "lab")
T_TWAP = 1786060800
T_5M60 = 1786665600
SCAN = [0.10 + 0.04 * i for i in range(16)]      # 10% … 70% de la ventana
DEV = (0.02, 0.04, 0.06, 0.10)                   # cuánto por debajo de la curva, en probabilidad
MOVE = (0.0, 5.0, 10.0)                          # movimiento del spot a favor en los últimos 10 s, $
SPREAD = 0.025                                   # spread máximo admitido
NB = 40                                          # tramos de la curva empírica


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
                if len(row) < 11 or not row[1].startswith("btc-updown-") or row[3] not in ("Up", "Down"):
                    continue
                try:
                    ts = int(row[0]); b = float(row[4]); a = float(row[10])
                except Exception: continue
                if not (0 < b < a < 1): continue
                raw.setdefault(row[1], {"cid": row[2], "Up": [], "Down": []})[row[3]].append((ts, b, a))
    W = {}
    for slug, d in raw.items():
        if not d["Up"] or not d["Down"]: continue
        d["Up"].sort(); d["Down"].sort(); W[slug] = d
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


HDR = (f"  {'caso':>26}{'n':>7}{'ask':>7}{'gana%':>7}{'EV':>8}{'tr':>7}{'te':>7}"
       f"{'-top1%':>8}{'sem+':>7}{'z':>7}")


def show(lab, s):
    if s is None: print(f"  {lab:>26}{'(pocos)':>9}"); return
    n, ask, g, ev, tr, te, e1, wk, z = s
    print(f"  {lab:>26}{n:>7}{ask:>7.3f}{g:>6.0f}%{ev:>+8.2f}{tr:>+7.2f}{te:>+7.2f}"
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

    # ---------- observaciones ----------
    OBS = []          # por ventana: lista de instantes con los dos tokens
    for slug, d in W.items():
        cid = d["cid"]; win = reso.get(cid)
        if win not in ("Up", "Down"): continue
        v = "5m" if "-5m-" in slug else "15m"
        ws = int(slug.split("-")[-1]); wlen = 300 if v == "5m" else 900; close = ws + wlen
        L = regime_L(v, ws); ref = avg(ws - L, ws)
        if ref is None: continue
        seq = []
        for f in SCAN:
            t = ws + int(f * wlen)
            if t > close - 40: continue
            cur = le(t); p10 = le(t - 10)
            if cur is None or p10 is None: continue
            tau = max((close - t) - 2 * L / 3.0, 1.0)
            row = {"ws": ws, "t": t, "x": {}, "ask": {}, "mid": {}, "won": {}, "sp": {}, "mv": {}}
            ok = True
            for X in ("Up", "Down"):
                rows = d[X]; k = bisect.bisect_left(rows, (t, 0, 0))
                if k >= len(rows) or rows[k][0] > t + 15 or rows[k][0] >= close - 20: ok = False; break
                _, b, a = rows[k]
                s = 1 if X == "Up" else -1
                row["x"][X] = s * (cur - ref) / math.sqrt(tau)     # margen normalizado por el tiempo que queda
                row["ask"][X] = a; row["mid"][X] = (a + b) / 2
                row["sp"][X] = a - b
                row["won"][X] = 1 if win == X else 0
                row["mv"][X] = s * (cur - p10)
            if ok: seq.append(row)
        if seq: OBS.append(seq)
    print(f"ventanas evaluables: {len(OBS)} · instantes: {sum(len(s) for s in OBS)}", flush=True)
    if len(OBS) < 500: print("muestra corta"); return

    # ---------- curva empírica del mercado, AJUSTADA SOLO EN LA 1ª MITAD ----------
    allws = sorted(seq[0]["ws"] for seq in OBS)
    cut = allws[len(allws) // 2]
    fit = []
    for seq in OBS:
        if seq[0]["ws"] >= cut: continue
        for row in seq:
            for X in ("Up", "Down"):
                if row["sp"][X] <= SPREAD: fit.append((row["x"][X], row["mid"][X]))
    fit.sort()
    if len(fit) < 5000: print("pocos puntos para la curva"); return
    edges = [fit[int(i * len(fit) / NB)][0] for i in range(1, NB)]
    cur_y = []
    for i in range(NB):
        lo = 0 if i == 0 else int(i * len(fit) / NB)
        hi = len(fit) if i == NB - 1 else int((i + 1) * len(fit) / NB)
        seg = sorted(y for _, y in fit[lo:hi])
        cur_y.append(seg[len(seg) // 2] if seg else 0.5)
    print(f"curva del mercado ajustada con {len(fit):,} puntos de la 1ª mitad "
          f"(x de {fit[0][0]:+.2f} a {fit[-1][0]:+.2f})", flush=True)

    def fair(x):
        return cur_y[bisect.bisect_right(edges, x)]

    # ---------- señal ----------
    def run(dev, mv, mirror=False):
        out = []
        for seq in OBS:
            hit = None
            for row in seq:
                for X in ("Up", "Down"):
                    if row["sp"][X] > SPREAD: continue
                    if row["mv"][X] < mv: continue
                    if fair(row["x"][X]) - row["ask"][X] >= dev:
                        hit = (row, X); break
                if hit: break
            if not hit: continue
            row, X = hit
            Z = ("Down" if X == "Up" else "Up") if mirror else X
            out.append({"ws": row["ws"], "ask": row["ask"][Z], "won": row["won"][Z]})
        return out

    print("\n" + "=" * 108)
    print(f"  COMPRAR EL ASK QUE SE HA QUEDADO POR DEBAJO DE LA CURVA DEL MERCADO (spread ≤{100*SPREAD:.1f}¢)")
    print("  · la curva se ajustó SOLO con la 1ª mitad, así que la columna 'te' es fuera de muestra de verdad")
    print("=" * 108)
    print(HDR)
    best = None
    for mv in MOVE:
        for dev in DEV:
            s = gates(run(dev, mv))
            lab = f"bajo curva ≥{100*dev:.0f}pp · BTC10s ≥{mv:.0f}$"
            show(lab, s)
            if s and (best is None or s[3] > best[1][3]): best = (lab, s, (dev, mv))
        print()
    if not best: print("ninguna combinación con muestra"); return
    print("=" * 108)
    print(f"  CONTROL ESPEJO de la mejor ({best[0]})")
    print("=" * 108)
    print(HDR)
    show("señal", best[1])
    show("espejo (debe perder)", gates(run(*best[2], mirror=True)))

    print("\nLECTURA: la curva sale de la 1ª mitad, así que 'te' es fuera de muestra REAL, no un corte")
    print("cosmético. Exijo meseta en la rejilla (no una casilla), EV+ en tr Y te, −top1% positivo, mayoría de")
    print("semanas y que el espejo pierda. Si sale, hay que comprobar en vivo lo único que estos datos no")
    print("pueden decir: cuánto AGUANTA esa cotización rancia, porque el libro del laboratorio va cada ~6 s y")
    print("el mecanismo vive en segundos — eso lo mide queuewatch, que ya graba al milisegundo.")


if __name__ == "__main__":
    try: sys.stdout.reconfigure(encoding="utf-8")
    except Exception: pass
    main()
