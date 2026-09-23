"""
freshcheck.py — markout.py dice que cotizando FRESCO (0-2 s) el market making es POSITIVO: markout +0,71/+0,73
y a resolución +0,22 (5m) / +0,36 (15m), con la selección adversa creciendo ~0,5pp por segundo de retraso.
Cuadra con la física (medio spread 0,52 + rebate 0,35 = 0,86 sin toxicidad; observamos 0,71 ⇒ adversa 0,15pp).
Es lo primero positivo del proyecto, así que hay que intentar tumbarlo ANTES de construir nada.

LA FORMA DE QUE SEA MENTIRA: las marcas de tiempo de la cinta y del libro van en SEGUNDOS ENTEROS y vienen de
sistemas distintos (el libro lo sella el colector al hacer poll; la operación la sella Polymarket). "Edad 0" =
mismo segundo, y dentro de ese segundo la operación pudo ocurrir ANTES de la foto ⇒ estaríamos vendiendo a un
precio que ya se movió por esa misma operación = look-ahead, y produciría exactamente este resultado.

DIAGNÓSTICO DECISIVO — EDADES NEGATIVAS. Repetimos el ejercicio usando fotos del libro tomadas DESPUÉS de la
operación (edad −1, −2, −3…). Esas están contaminadas por construcción, así que marcan el listón de lo que
parece un artefacto. Entonces:
   · si edad 0 se parece a edad −1 → contaminación, el hallazgo muere.
   · si edad 0 se parece a edad +1 y +2, y hay un escalón claro entre −1 y 0 → el hallazgo es REAL.
Además: desglose segundo a segundo, semanas positivas del tramo fresco, y la economía por ventana.

MEMORIA: la primera versión tumbó la Pi (acumulaba un dict por operación × foto candidata ≈ 8 M de objetos).
Esta agrega AL VUELO en contadores y guarda cinta y libro en `array` compactos. Nada crece con los datos.
Deduplicación: por (ts, lado, precio) adyacentes dentro de cada (cid, outcome) en vez de un set global de
claves — evita un set de millones de entradas a cambio de fundir operaciones idénticas en el mismo segundo.

    cd ~/polymarket-btc-up-down/research && python3 freshcheck.py
"""
import csv, os, sys, glob, time, bisect
from array import array

DIR = os.path.join(os.path.dirname(__file__), "lab")
DMIN, DMAX = -4, 10          # edad con signo: negativa = libro POSTERIOR a la operación
HOR = (5, 15)
PB = [("<0,30", 0, .30), ("0,30-0,50", .30, .50), ("0,50-0,70", .50, .70),
      ("0,70-0,90", .70, .90), (">0,90", .90, 1.0)]
FRESH = (1, 2)               # el tramo "honesto y fresco"


def fee(p): return 0.07 * p * (1 - p)
def rebate(p): return 0.20 * fee(p)
def week(ts): return time.strftime("%Y-%U", time.gmtime(ts))


class Acc:
    """contadores corrientes: nada crece con el número de operaciones."""
    __slots__ = ("n", "nf", "spx", "ssp", "n5", "s5", "n15", "s15", "nr", "sr")

    def __init__(self):
        self.n = self.nf = 0
        self.spx = self.ssp = 0.0
        self.n5 = self.n15 = self.nr = 0
        self.s5 = self.s15 = self.sr = 0.0

    def add(self, filled, px, sp, mk5, mk15, res):
        self.n += 1
        if not filled: return
        self.nf += 1; self.spx += px; self.ssp += sp
        if mk5 is not None: self.n5 += 1; self.s5 += mk5
        if mk15 is not None: self.n15 += 1; self.s15 += mk15
        if res is not None: self.nr += 1; self.sr += res

    def m(self, which, minn=30):
        n, s = {"5": (self.n5, self.s5), "15": (self.n15, self.s15),
                "r": (self.nr, self.sr)}[which]
        return 100 * s / n if n >= minn else None


def load_tape():
    """(cid, outcome) -> (array ts, array isbuy, array px), ordenado y sin duplicados adyacentes."""
    raw = {}
    nrow = 0
    for path in sorted(glob.glob(os.path.join(DIR, "wintrades_*.csv"))):
        with open(path, encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                oc = r.get("outcome")
                if oc not in ("Up", "Down"): continue
                sd = (r.get("trade_side") or r.get("side") or "").upper()
                if sd not in ("BUY", "SELL"): continue
                try: ts = int(float(r["ts_trade"])); pr = float(r["price"])
                except Exception: continue
                raw.setdefault((r["cid"], oc), []).append((ts, 1 if sd == "BUY" else 0, pr))
                nrow += 1
    T = {}
    for k, v in raw.items():
        v.sort()
        ts = array("i"); ib = array("b"); px = array("f")
        last = None
        for row in v:
            if row == last: continue
            last = row
            ts.append(row[0]); ib.append(row[1]); px.append(row[2])
        T[k] = (ts, ib, px)
    del raw
    return T, nrow


def load_books():
    """(slug, side) -> (cid, array ts, array bid, array ask)"""
    raw = {}
    for path in sorted(glob.glob(os.path.join(DIR, "books_*.csv"))):
        with open(path, encoding="utf-8") as fh:
            rd = csv.reader(fh); next(rd, None)
            for row in rd:
                if len(row) < 11 or not row[1].startswith("btc-updown-") or row[3] not in ("Up", "Down"):
                    continue
                try:
                    ts = int(row[0]); b = float(row[4]) if row[4] else None
                    a = float(row[10]) if row[10] else None
                except Exception: continue
                if a is None or b is None or not (0 < b < a < 1): continue
                raw.setdefault((row[1], row[3]), [row[2], []])[1].append((ts, b, a))
    B = {}
    for k, (cid, v) in raw.items():
        v.sort()
        ts = array("i"); bd = array("f"); ak = array("f")
        for t, b, a in v:
            ts.append(t); bd.append(b); ak.append(a)
        B[k] = (cid, ts, bd, ak)
    del raw
    return B


def fmt(x, w=9, d=2):
    return f"{x:>+{w}.{d}f}" if x is not None else f"{'—':>{w}}"


def main():
    print("cargando cinta…", flush=True)
    TP, nrow = load_tape()
    print(f"  operaciones: {nrow} · series (cid,lado): {len(TP)}", flush=True)
    print("cargando libro…", flush=True)
    B = load_books()
    print(f"  series de libro: {len(B)}", flush=True)

    reso = {}
    for fn in ("clob_reso_mmtoxic.csv", "clob_reso_mmlogs.csv", "clob_reso_mw.csv",
               "clob_reso_win.csv", "clob_reso_tape.csv", "clob_reso_uni.csv"):
        p = os.path.join(DIR, fn)
        if os.path.exists(p):
            for r in csv.DictReader(open(p, encoding="utf-8")):
                if r.get("winner"): reso[r["cid"]] = r["winner"]
    print(f"  resoluciones en caché: {len(reso)}", flush=True)

    AD = {}      # (v, d)            -> Acc
    AW = {}      # (v, semana)       -> Acc   (solo d fresco)
    AP = {}      # (v, zona)         -> Acc   (solo d fresco)
    WINS = {"5m": set(), "15m": set()}
    NTR = {"5m": 0, "15m": 0}

    def acc(D, k):
        a = D.get(k)
        if a is None: a = D[k] = Acc()
        return a

    nb = 0
    for (slug, side), (cid, tss, bids, asks) in B.items():
        tp = TP.get((cid, side))
        if tp is None: continue
        nb += 1
        if nb % 5000 == 0: print(f"   … {nb} series", flush=True)
        v = "5m" if "-5m-" in slug else "15m"
        ws = int(slug.split("-")[-1]); close = ws + (300 if v == "5m" else 900)
        WINS[v].add(ws)
        win = reso.get(cid); wside = None
        if win in ("Up", "Down"): wside = 1 if win == side else 0
        wk = week(ws)
        tts, tib, tpx = tp
        ntss = len(tss)

        def mid_at(t):
            k = bisect.bisect_right(tss, t) - 1
            if k < 0 or t - tss[k] > 20 or tss[k] > close - 5: return None
            return (bids[k] + asks[k]) / 2

        for i in range(len(tts)):
            ts = tts[i]
            if ts > close - 5: continue
            isbuy = tib[i]; pr = tpx[i]
            NTR[v] += 1
            lo = bisect.bisect_left(tss, ts - DMAX)
            hi = bisect.bisect_right(tss, ts - DMIN)
            m5 = m15 = None; got = False
            for k in range(lo, min(hi, ntss)):
                d = ts - tss[k]
                if not (DMIN <= d <= DMAX) or tss[k] > close - 5: continue
                a = asks[k]; b = bids[k]
                if isbuy: filled = pr >= a - 1e-9; pos, px = -1, a
                else:     filled = pr <= b + 1e-9; pos, px = 1, b
                mk5 = mk15 = res = None
                if filled:
                    if not got:
                        m5 = mid_at(ts + 5); m15 = mid_at(ts + 15); got = True
                    rb = rebate(px)
                    if m5 is not None: mk5 = pos * (m5 - px) + rb
                    if m15 is not None: mk15 = pos * (m15 - px) + rb
                    if wside is not None: res = pos * (wside - px) + rb
                acc(AD, (v, d)).add(filled, px, a - b, mk5, mk15, res)
                if filled and d in FRESH:
                    acc(AW, (v, wk)).add(True, px, a - b, mk5, mk15, res)
                    for nm, plo, phi in PB:
                        if plo <= px < phi:
                            acc(AP, (v, nm)).add(True, px, a - b, mk5, mk15, res)
                            break

    # ============ 1) DIAGNÓSTICO ============
    print("\n" + "=" * 104)
    print("  1) EDAD CON SIGNO, SEGUNDO A SEGUNDO · d<0 = libro tomado DESPUÉS de la operación (contaminado")
    print("     por construcción, marca el listón del artefacto) · d>0 = libro ANTES (honesto)")
    print("=" * 104)
    for v in ("5m", "15m"):
        print(f"\n  ── {v} ──")
        print(f"  {'edad d':>8}{'registros':>11}{'%relleno':>10}{'n rellenos':>12}{'precio':>8}"
              f"{'mk 5s':>9}{'mk 15s':>9}{'a resol.':>10}")
        for d in range(DMIN, DMAX + 1):
            a = AD.get((v, d))
            if not a or a.n == 0: continue
            print(f"  {d:>8}{a.n:>11}{100*a.nf/a.n:>9.0f}%{a.nf:>12}"
                  f"{(a.spx/a.nf if a.nf else float('nan')):>8.3f}"
                  f"{fmt(a.m('5'))}{fmt(a.m('15'))}{fmt(a.m('r'), 10)}")
    print("\n  → si d=0 se parece a d=−1 ⇒ CONTAMINACIÓN (muere). Si hay escalón entre d=−1 y d=0, y d=0 se")
    print("    parece a d=+1/+2 ⇒ el hallazgo de markout.py es REAL.")

    # ============ 2) SOLO HONESTOS ============
    print("\n" + "=" * 104)
    print(f"  2) SOLO LIBRO ANTERIOR A LA OPERACIÓN (d ∈ {{{FRESH[0]},{FRESH[1]}}}) — por semanas y por precio")
    print("=" * 104)
    for v in ("5m", "15m"):
        ks = sorted(k for k in AW if k[0] == v)
        if not ks: continue
        nf = sum(AW[k].nf for k in ks)
        px = sum(AW[k].spx for k in ks) / nf if nf else float("nan")
        sp = sum(AW[k].ssp for k in ks) / nf if nf else float("nan")
        print(f"\n  ── {v} ── (n {nf} · precio {px:.3f} · spread {100*sp:.2f}¢)")
        print(f"  {'semana':>10}{'n':>9}{'mk 5s':>9}{'mk 15s':>9}{'a resol.':>10}")
        wp = wt = 0
        for k in ks:
            a = AW[k]; c = a.m("5")
            if c is not None:
                wt += 1; wp += 1 if c > 0 else 0
            print(f"  {k[1]:>10}{a.nf:>9}{fmt(c)}{fmt(a.m('15'))}{fmt(a.m('r'), 10)}")
        print(f"  semanas con markout positivo: {wp}/{wt}")
        print(f"\n  {'zona precio':>10}{'n':>9}{'mk 5s':>9}{'mk 15s':>9}{'a resol.':>10}")
        for nm, _, _ in PB:
            a = AP.get((v, nm))
            if not a or a.nf < 50: continue
            print(f"  {nm:>10}{a.nf:>9}{fmt(a.m('5'))}{fmt(a.m('15'))}{fmt(a.m('r'), 10)}")

    # ============ 3) ECONOMÍA ============
    print("\n" + "=" * 104)
    print(f"  3) ECONOMÍA cotizando fresco (d ∈ {{{FRESH[0]},{FRESH[1]}}}) — qué da por ventana si lo montamos")
    print("=" * 104)
    print(f"  {'mercado':>9}{'ventanas':>10}{'oper/vent':>11}{'rellenos/vent':>15}"
          f"{'mk5/relleno':>14}{'pp/ventana':>13}{'(1 de cada 4)':>16}")
    for v in ("5m", "15m"):
        nw = len(WINS[v])
        ks = [k for k in AW if k[0] == v]
        if not nw or not ks: continue
        nf = sum(AW[k].nf for k in ks)
        n5 = sum(AW[k].n5 for k in ks); s5 = sum(AW[k].s5 for k in ks)
        if n5 < 100: continue
        c = 100 * s5 / n5
        print(f"  {v:>9}{nw:>10}{NTR[v]/nw:>11.1f}{nf/nw:>15.2f}{c:>+14.2f}"
              f"{c*nf/nw:>+13.1f}{c*nf/nw/4:>+16.1f}")
    print("  (la última columna aplica el relleno real medido ~22,7%: solo ganamos 1 de cada 4 colas)")

    print("\nLECTURA: todo depende del escalón entre d=−1 y d=0/+1 del bloque 1. Si está, tenemos por primera vez")
    print("un edge de maker medido honestamente y el trabajo pasa a ser de INGENIERÍA: recotizar por WSS en")
    print("menos de 2 s. Si no está, el +0,7 era la marca de tiempo y seguimos buscando.")


if __name__ == "__main__":
    try: sys.stdout.reconfigure(encoding="utf-8")
    except Exception: pass
    main()
