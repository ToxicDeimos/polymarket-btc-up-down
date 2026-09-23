"""
clocklag.py — ¿ESTÁN ALINEADOS LOS RELOJES? freshcheck dejó el hallazgo en empate técnico y hay que romperlo.

markout.py: cotizando "fresco" (0-2 s) el maker gana +0,7pp; a partir de 3 s se hunde. El perfil es MESETA y
PRECIPICIO, con el salto justo en d=3 (y ahí también salta el % de relleno: 49%→54%→62%). Dos lecturas
incompatibles y el dato no elige:
  · REAL: los informados tardan ~3 s en reaccionar a BTC ⇒ una orden de <3 s está a salvo ⇒ hay edge.
  · DESFASE: las marcas del libro las pone NUESTRO colector y las de la cinta las pone POLYMARKET. Si van ~3 s
    desalineadas, lo que llamamos d≤2 es en realidad POSTERIOR a la operación, el cero real está en d=3, y
    ahí el markout es +0,10 ⇒ no hay edge, era look-ahead.
(El test de edades negativas de freshcheck NO discrimina: con d<0 el markout solo cubre 1-2 s, así que mide la
asíntota de latencia cero, no el listón del artefacto. Error mío de diseño.)

DOS MEDIDAS QUE SÍ DECIDEN, ninguna interpretable de dos formas:

 1) EL COMPRADOR AGRESIVO PAGA EL ASK. Para cada d, ¿con qué frecuencia el precio de la operación coincide con
    el ask cotizado (y el de venta con el bid)? El d donde la coincidencia es MÁXIMA es el instante real de la
    operación. Pico en d=1 → relojes alineados, hallazgo REAL. Pico en d=3/4 → desfase de ~3 s, espejismo.

 2) UNA OPERACIÓN NO MUEVE EL LIBRO ANTES DE OCURRIR. Estudio de evento: medio del libro (con signo según la
    dirección de la operación) frente a una base lejana, para cada d. La curva tiene que ser PLANA antes y
    subir DESPUÉS. Donde arranca la subida está el instante real. Si sube ya en d=+3, el libro "reacciona"
    antes de la operación = imposible = desfase.

Usamos operaciones AISLADAS (sin otra en ±12 s sobre el mismo token) para que el estudio de evento no mezcle
el impacto de operaciones vecinas. Memoria acotada: contadores, arrays compactos, nada crece con los datos.

    cd ~/polymarket-btc-up-down/research && python3 clocklag.py
"""
import csv, os, sys, glob, time, bisect
from array import array

DIR = os.path.join(os.path.dirname(__file__), "lab")
DLO, DHI = -10, 12         # d = ts_operación − ts_libro  (d>0: libro ANTES según las marcas)
TOL = 0.005                # "coincide con el ask"
ISO = 12                   # aislamiento, s
BASE = 20                  # base del estudio de evento, s antes


def mean(s, n, minn=50): return (s / n) if n >= minn else None
def fmt(x, w=9, d=2, mul=100):
    return f"{x*mul:>+{w}.{d}f}" if x is not None else f"{'—':>{w}}"


def load_tape():
    raw = {}
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
    return T


def load_books():
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


def main():
    print("cargando cinta…", flush=True)
    TP = load_tape()
    print(f"  series (cid,lado): {len(TP)}", flush=True)
    print("cargando libro…", flush=True)
    B = load_books()
    print(f"  series de libro: {len(B)}", flush=True)

    # contadores por (v, d)
    MT = {}   # coincidencia precio↔cotización de su propio lado: [n, n_match, sum|dif|]
    EV = {}   # estudio de evento: [n, suma de (medio − base) con signo]
    SP = {}   # spread medio por d (control de sanidad)

    def bump(D, k, idx, val, size):
        a = D.get(k)
        if a is None: a = D[k] = [0.0] * size
        a[idx] += val

    nb = 0; ntr = 0; niso = 0
    for (slug, side), (cid, tss, bids, asks) in B.items():
        tp = TP.get((cid, side))
        if tp is None: continue
        nb += 1
        if nb % 5000 == 0: print(f"   … {nb} series", flush=True)
        v = "5m" if "-5m-" in slug else "15m"
        ws = int(slug.split("-")[-1]); close = ws + (300 if v == "5m" else 900)
        tts, tib, tpx = tp
        n = len(tts)
        for i in range(n):
            ts = tts[i]
            if ts > close - 5 or ts < ws: continue
            ntr += 1
            isolated = ((i == 0 or ts - tts[i - 1] > ISO) and
                        (i == n - 1 or tts[i + 1] - ts > ISO))
            isbuy = tib[i]; pr = tpx[i]; sgn = 1 if isbuy else -1
            if isolated:
                niso += 1
                kb = bisect.bisect_right(tss, ts - BASE) - 1
                base = (bids[kb] + asks[kb]) / 2 if (kb >= 0 and ts - BASE - tss[kb] <= 15) else None
            else:
                base = None
            lo = bisect.bisect_left(tss, ts - DHI)
            hi = bisect.bisect_right(tss, ts - DLO)
            for k in range(lo, min(hi, len(tss))):
                d = ts - tss[k]
                if not (DLO <= d <= DHI) or tss[k] > close - 2: continue
                a = asks[k]; b = bids[k]
                q = a if isbuy else b
                key = (v, d)
                bump(MT, key, 0, 1, 3)
                bump(MT, key, 1, 1 if abs(pr - q) <= TOL else 0, 3)
                bump(MT, key, 2, abs(pr - q), 3)
                bump(SP, key, 0, 1, 2); bump(SP, key, 1, a - b, 2)
                if base is not None:
                    bump(EV, key, 0, 1, 2)
                    bump(EV, key, 1, sgn * ((a + b) / 2 - base), 2)
    print(f"operaciones consideradas: {ntr} · aisladas (±{ISO}s): {niso} "
          f"({100*niso/max(1,ntr):.0f}%)", flush=True)

    print("\n" + "=" * 96)
    print("  1) ¿DÓNDE COINCIDE EL PRECIO DE LA OPERACIÓN CON LA COTIZACIÓN DE SU PROPIO LADO?")
    print("     (compra ↔ ask, venta ↔ bid · el máximo marca el INSTANTE REAL de la operación)")
    print("=" * 96)
    for v in ("5m", "15m"):
        print(f"\n  ── {v} ──")
        print(f"  {'d':>5}{'n':>11}{'coincide':>10}{'|dif| media':>13}{'spread':>9}   perfil")
        best = None
        for d in range(DLO, DHI + 1):
            a = MT.get((v, d))
            if not a or a[0] < 200: continue
            rate = a[1] / a[0]
            if best is None or rate > best[1]: best = (d, rate)
        for d in range(DLO, DHI + 1):
            a = MT.get((v, d)); s = SP.get((v, d))
            if not a or a[0] < 200: continue
            rate = a[1] / a[0]
            bar = "█" * int(round(60 * rate / max(best[1], 1e-9)))
            mark = "  ← máximo" if d == best[0] else ""
            print(f"  {d:>5}{int(a[0]):>11}{100*rate:>9.1f}%{100*a[2]/a[0]:>12.2f}¢"
                  f"{100*s[1]/s[0]:>8.2f}¢   {bar}{mark}")
        if best:
            print(f"  → máximo en d={best[0]}. Alineado ⇒ d=1 (la foto inmediatamente anterior). "
                  f"Desfase ⇒ d≥3.")

    print("\n" + "=" * 96)
    print("  2) ESTUDIO DE EVENTO: medio del libro con signo, frente a la base de 20 s antes")
    print("     (una operación NO puede mover el libro ANTES de ocurrir: plano a la izquierda, sube a la")
    print("      derecha · d GRANDE = libro viejo = ANTES según las marcas)")
    print("=" * 96)
    for v in ("5m", "15m"):
        print(f"\n  ── {v} ── (operaciones aisladas)")
        print(f"  {'d':>5}{'n':>10}{'medio−base':>12}   perfil (0 = ─, + a la derecha)")
        vals = {}
        for d in range(DHI, DLO - 1, -1):
            a = EV.get((v, d))
            if not a or a[0] < 100: continue
            vals[d] = a[1] / a[0]
        if not vals: continue
        mx = max(abs(x) for x in vals.values()) or 1e-9
        for d in sorted(vals, reverse=True):
            x = vals[d]; a = EV[(v, d)]
            w = int(round(24 * x / mx))
            bar = (" " * (24 + min(w, 0))) + ("█" * abs(w)) if w else " " * 24 + "─"
            print(f"  {d:>5}{int(a[0]):>10}{100*x:>+11.2f}¢   {bar}")
        print("  → el libro debe estar PLANO en los d grandes (antes) y haber subido en los d pequeños y")
        print("    negativos (después). Si ya ha subido en d=+3, reacciona antes de la operación = DESFASE.")

    print("\nLECTURA: las dos medidas tienen que señalar el mismo instante. Si ambas dicen d≈1, los relojes")
    print("están alineados, el markout fresco de +0,7pp es REAL y el problema pasa a ser de ingeniería")
    print("(recotizar por WSS en <2 s). Si señalan d≈3-4, el colector va ~3 s por detrás de Polymarket, el")
    print("cero verdadero está en el precipicio y el +0,7 era look-ahead: no hay edge de maker y hay que")
    print("recalcular TODO lo que use la edad del libro con el desfase corregido.")


if __name__ == "__main__":
    try: sys.stdout.reconfigure(encoding="utf-8")
    except Exception: pass
    main()
