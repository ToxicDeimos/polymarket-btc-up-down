"""
booklag.py — ¿CUÁNTO TARDA EL LIBRO EN REPRECIAR? La medición que cierra el día, y no depende de ninguna
hipótesis mía: son dos series de tiempo en el MISMO reloj, se mira el desfase entre ellas y punto.

Por qué hacía falta: wallet1 encontró una wallet que compra 9.366 veces, cruza el spread, paga comisión y
bate el precio que paga en +2,6 a +3,6pp en las SIETE franjas de precio, con z +3,26 y +3,20 en dos mitades
independientes. Una ventaja PLANA en todo el rango no es señal direccional: es una propiedad de la ORDEN.
Mi explicación es el libro rancio — markout midió que una cotización vieja le cuesta al maker ~1pp por
segundo, y esa pérdida es la ganancia del que se la quita. stalepick no pudo probarlo porque el laboratorio
graba libro Y spot cada ~6-7 s y el fenómeno vive en 1-3 s: instrumento más lento que el fenómeno.
queuewatch ya graba las dos series al milisegundo.

 A) DESFASE — correlación cruzada entre los cambios del spot y los del medio de Polymarket, por retardo.
    El retardo que maximiza la correlación ES el tiempo de reacción del libro.
 B) ESTUDIO DE SALTOS — cuando BTC se mueve ≥$X en un segundo, qué fracción del movimiento del libro ya ha
    ocurrido a los 100 ms, 200 ms, 500 ms, 1 s, 2 s… Es la curva que decide: lo que queda por recorrer
    cuando nosotros podemos llegar es lo que se puede capturar.
 C) LA CUENTA — el margen capturable a cada latencia, en puntos, frente al peaje de cruzar (medio spread +
    comisión ≈1,6pp). Si a 200 ms quedan 3pp por recorrer, hay negocio; si quedan 0,5pp, no lo hay.

    cd ~/polymarket-btc-up-down/research && python3 booklag.py
"""
import csv, os, sys, glob, math, bisect
from array import array

DIR = os.path.dirname(__file__)
LAGS = [0.0, 0.1, 0.2, 0.3, 0.5, 0.75, 1.0, 1.5, 2.0, 3.0, 5.0]
JUMPS = (5.0, 10.0, 20.0)     # tamaño del salto de BTC, $
JW = 1.0                      # ventana del salto, s
HOR = 8.0                     # hasta dónde seguimos el libro tras el salto, s


def mean(xs): return sum(xs) / len(xs) if xs else float("nan")


def main():
    paths = sorted(glob.glob(os.path.join(DIR, "queue_events_*.csv")))
    if not paths: print("no hay queue_events_*.csv"); return
    SP = []                      # (ts, precio BTC)
    MD = {}                      # ws -> {tok: [(ts, medio)]}
    for p in paths:
        with open(p, encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                try:
                    t = float(r["ts"]); typ = r["typ"]
                except Exception: continue
                if typ == "S":
                    try: SP.append((t, float(r["price"])))
                    except Exception: pass
                elif typ in ("C", "B"):
                    bb = r.get("bb"); ba = r.get("ba")
                    if not bb or not ba: continue
                    try:
                        m = (float(bb) + float(ba)) / 2; ws = int(r["ws"]); tok = r["tok"]
                    except Exception: continue
                    if not (0 < m < 1): continue
                    MD.setdefault(ws, {}).setdefault(tok, []).append((t, m))
    SP.sort()
    for ws in MD:
        for tok in MD[ws]: MD[ws][tok].sort()
    nmid = sum(len(v) for d in MD.values() for v in d.values())
    print(f"filas de spot: {len(SP):,} · puntos de medio: {nmid:,} · ventanas: {len(MD)}", flush=True)
    if len(SP) < 2000 or nmid < 20000:
        print("muestra corta — dejar grabando más tiempo"); return
    sts = array("d", [t for t, _ in SP]); spx = array("d", [p for _, p in SP])
    gap = [sts[i + 1] - sts[i] for i in range(0, min(len(sts) - 1, 20000))]
    gap.sort()
    print(f"cadencia del spot: mediana {1000*gap[len(gap)//2]:.0f} ms · "
          f"p90 {1000*gap[int(.9*len(gap))]:.0f} ms", flush=True)

    def sp_at(t):
        i = bisect.bisect_right(sts, t) - 1
        return spx[i] if (i >= 0 and t - sts[i] <= 2.0) else None

    # ---------- A) correlación cruzada ----------
    print("\n" + "=" * 88)
    print("  A) DESFASE: correlación entre el cambio del spot y el del medio de Polymarket")
    print("=" * 88)
    print(f"  {'retardo':>10}{'n':>9}{'correlación':>14}   perfil")
    best = None; res = []
    for L in LAGS:
        xs = []; ys = []
        for ws, d in MD.items():
            ser = d.get("Up")
            if not ser or len(ser) < 50: continue
            step = max(1, len(ser) // 400)
            for i in range(step, len(ser), step):
                t1, m1 = ser[i - step]; t2, m2 = ser[i]
                if not (0.3 <= t2 - t1 <= 3.0): continue
                a = sp_at(t1 - L); b = sp_at(t2 - L)
                if a is None or b is None: continue
                xs.append(b - a); ys.append(m2 - m1)
        if len(xs) < 300: res.append((L, len(xs), None)); continue
        mx = mean(xs); my = mean(ys)
        sx = math.sqrt(mean([(v - mx) ** 2 for v in xs])) or 1e-12
        sy = math.sqrt(mean([(v - my) ** 2 for v in ys])) or 1e-12
        c = mean([(a - mx) * (b - my) for a, b in zip(xs, ys)]) / (sx * sy)
        res.append((L, len(xs), c))
        if best is None or c > best[1]: best = (L, c)
    mxc = max((c for _, _, c in res if c is not None), default=1e-9) or 1e-9
    for L, n, c in res:
        bar = "█" * int(round(50 * max(c, 0) / mxc)) if c is not None else ""
        mark = "  ← máximo" if (best and abs(L - best[0]) < 1e-9) else ""
        print(f"  {L:>8.2f}s{n:>9}{(f'{c:+.3f}' if c is not None else '—'):>14}   {bar}{mark}")
    if best:
        print(f"  → el libro reacciona con ~{best[0]:.2f}s de retardo (correlación {best[1]:+.3f})")

    # ---------- B) estudio de saltos ----------
    print("\n" + "=" * 88)
    print("  B) TRAS UN SALTO DE BTC: ¿qué parte del movimiento del libro ya ha ocurrido?")
    print("=" * 88)
    for J in JUMPS:
        ev = []
        for ws, d in MD.items():
            ser = d.get("Up")
            if not ser or len(ser) < 50: continue
            t0s = ser[0][0]; t1s = ser[-1][0]
            i = bisect.bisect_left(sts, t0s)
            last_ev = -99
            while i < len(sts) and sts[i] < t1s - HOR:
                t = sts[i]
                a = sp_at(t - JW)
                if a is not None and abs(spx[i] - a) >= J and t - last_ev > HOR:
                    sgn = 1 if spx[i] > a else -1
                    def mid_at(tt):
                        k = bisect.bisect_right(ser, (tt, 9)) - 1
                        return ser[k][1] if (k >= 0 and tt - ser[k][0] <= 3.0) else None
                    m0 = mid_at(t); mend = mid_at(t + HOR)
                    if m0 is not None and mend is not None and abs(mend - m0) > 1e-9:
                        traj = {}
                        for L in LAGS:
                            mm = mid_at(t + L)
                            if mm is not None: traj[L] = sgn * (mm - m0)
                        if traj: ev.append((sgn * (mend - m0), traj))
                        last_ev = t
                i += 1
        if len(ev) < 30:
            print(f"\n  saltos ≥${J:.0f}: solo {len(ev)} — muestra corta"); continue
        tot = mean([e[0] for e in ev])
        print(f"\n  saltos ≥${J:.0f} en {JW:.0f}s · n={len(ev)} · movimiento total del libro a los "
              f"{HOR:.0f}s: {100*tot:+.2f}pp")
        print(f"  {'a los':>10}{'n':>8}{'recorrido':>12}{'% del total':>13}{'queda por recorrer':>21}")
        for L in LAGS:
            v = [e[1][L] for e in ev if L in e[1]]
            if len(v) < 20: continue
            m = mean(v)
            print(f"  {L:>8.2f}s{len(v):>8}{100*m:>+12.2f}{100*m/tot if tot else float('nan'):>12.0f}%"
                  f"{100*(tot-m):>+21.2f}")

    print("\n" + "=" * 88)
    print("  C) LA CUENTA: peaje de cruzar ≈ medio spread (0,5pp) + comisión (~1,2pp) ≈ 1,6-1,8pp")
    print("=" * 88)
    print("  Si a 200-500 ms QUEDA POR RECORRER bastante más que el peaje, el negocio de la wallet grande es")
    print("  ese y la Pi llega. Si lo que queda es menos que el peaje, su ventaja viene de otro sitio o exige")
    print("  una latencia que no tenemos, y el +3pp plano habrá que explicarlo de otra manera.")
    print("  Ojo: el desfase de A lleva dentro la latencia de red hasta Binance, pero las dos series entran")
    print("  por el mismo cable y se sellan con el mismo reloj, así que la diferencia ENTRE ellas es limpia.")


if __name__ == "__main__":
    try: sys.stdout.reconfigure(encoding="utf-8")
    except Exception: pass
    main()
