"""
askrace.py — ¿CUÁNTO SOBREVIVE LA COTIZACIÓN RANCIA? Última incógnita antes de decidir si se construye.

Lo que ya sabemos:
  · el mecanismo existe — stalebt: neto +1,70pp a 0,10 s, +0,72 a 0,20, cruce en 0,30, con el espejo
    perdiendo 2,7-5,7pp en todas las filas y ~100 acciones de tamaño en el ask;
  · la latencia NO es el problema — latency: firmar 0,9 ms + mandar la orden 52 + recibir el libro 25 = 77 ms
    (y sumando idas y vueltas completas en vez de solo la ida, así que el real ronda los 40).

Lo que falta: stalebt mira QUÉ PRECIO HABÍA, no si ese ask SIGUE AHÍ cuando llegamos. Venía diciendo que eso
solo lo contesta una orden real — me equivocaba: queuewatch graba también las operaciones, así que se puede
cronometrar. Tras un salto de BTC, para el mejor ask del lado favorecido se mide:

    · cuándo alguien lo LEVANTA (primera operación a ese precio o mejor)  → nos ganaron la carrera
    · cuándo el maker lo RETIRA o repreçia (el mejor ask se mueve)        → se evapora solo
    · lo primero de las dos = la VENTANA DE OPORTUNIDAD real

y se reporta qué fracción sigue viva a los 77 ms (lo nuestro), y a 100, 200, 300 y 500.

Si la mediana de supervivencia es de varios cientos de ms, llegamos de sobra. Si se evapora en decenas,
competimos contra gente dentro del centro de datos y da igual lo que corramos.

    cd ~/polymarket-btc-up-down/research && python3 askrace.py
"""
import csv, os, sys, glob, bisect
from array import array

DIR = os.path.dirname(__file__)
JUMPS = (5.0, 10.0)
JW = 1.0
COOL = 10.0
HOR = 5.0                      # hasta cuándo seguimos la cotización, s
MARKS = (0.077, 0.100, 0.200, 0.300, 0.500, 1.000)
OURS = 0.077


def med(xs):
    s = sorted(xs); return s[len(s) // 2] if s else float("nan")
def q(xs, p):
    s = sorted(xs); return s[min(len(s) - 1, int(p * len(s)))] if s else float("nan")
def mean(xs): return sum(xs) / len(xs) if xs else float("nan")


def main():
    paths = sorted(glob.glob(os.path.join(DIR, "queue_events_*.csv")))
    if not paths: print("no hay queue_events_*.csv"); return
    SP = []; BK = {}; TR = {}
    for p in paths:
        with open(p, encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                try: t = float(r["ts"]); typ = r["typ"]
                except Exception: continue
                if typ == "S":
                    try: SP.append((t, float(r["price"])))
                    except Exception: pass
                elif typ == "T":
                    try: TR.setdefault((int(r["ws"]), r["tok"]), []).append((t, float(r["price"])))
                    except Exception: pass
                elif typ in ("B", "C"):
                    ba = r.get("ba")
                    if not ba: continue
                    try: BK.setdefault((int(r["ws"]), r["tok"]), []).append((t, float(ba)))
                    except Exception: pass
    SP.sort()
    for k in BK: BK[k].sort()
    for k in TR: TR[k].sort()
    print(f"spot: {len(SP):,} · series de mejor ask: {len(BK)} · series de cinta: {len(TR)}", flush=True)
    if len(SP) < 2000: print("muestra corta — dejar grabando más"); return
    sts = array("d", [t for t, _ in SP]); spx = array("d", [p for _, p in SP])

    def sp_at(t):
        i = bisect.bisect_right(sts, t) - 1
        return spx[i] if (i >= 0 and t - sts[i] <= 2.0) else None

    wss = sorted(set(k[0] for k in BK))
    for J in JUMPS:
        LIFT = []; MOVE = []; WIN = []; nlift = 0; nmove = 0; nsurv = 0
        for ws in wss:
            ser_up = BK.get((ws, "Up")); ser_dn = BK.get((ws, "Down"))
            if not ser_up or not ser_dn: continue
            t0 = min(ser_up[0][0], ser_dn[0][0]); t1 = max(ser_up[-1][0], ser_dn[-1][0])
            i = bisect.bisect_left(sts, t0); last = -99
            while i < len(sts) and sts[i] < t1 - HOR:
                t = sts[i]; a = sp_at(t - JW)
                if a is None or abs(spx[i] - a) < J or t - last <= COOL:
                    i += 1; continue
                last = t
                tok = "Up" if spx[i] > a else "Down"
                ser = BK[(ws, tok)]
                k = bisect.bisect_right(ser, (t, 9)) - 1
                if k < 0 or t - ser[k][0] > 3.0: i += 1; continue
                A = ser[k][1]                                  # el ask rancio que querríamos levantar
                # ¿cuándo se mueve el mejor ask?
                tmove = None
                for j in range(k + 1, len(ser)):
                    if ser[j][0] > t + HOR: break
                    if abs(ser[j][1] - A) > 1e-9: tmove = ser[j][0] - t; break
                # ¿cuándo lo levanta alguien?
                tlift = None
                tr = TR.get((ws, tok)) or []
                m = bisect.bisect_left(tr, (t, 0))
                for j in range(m, len(tr)):
                    if tr[j][0] > t + HOR: break
                    if tr[j][1] >= A - 1e-9: tlift = tr[j][0] - t; break
                if tlift is not None: LIFT.append(tlift); nlift += 1
                if tmove is not None: MOVE.append(tmove); nmove += 1
                w = min([x for x in (tlift, tmove) if x is not None], default=None)
                if w is None: nsurv += 1
                else: WIN.append(w)
                i += 1
        n = len(WIN) + nsurv
        if n < 25:
            print(f"\nsaltos ≥${J:.0f}: solo {n} — muestra corta"); continue
        print("\n" + "=" * 92)
        print(f"  SALTOS DE BTC ≥${J:.0f} en {JW:.0f}s · n={n} · ¿cuánto vive el ask que queremos levantar?")
        print("=" * 92)
        if LIFT:
            print(f"  alguien lo LEVANTA en {nlift} de {n} casos ({100*nlift/n:.0f}%) · "
                  f"p10 {1000*q(LIFT,.10):.0f} ms · mediana {1000*med(LIFT):.0f} · p90 {1000*q(LIFT,.90):.0f}")
        if MOVE:
            print(f"  el maker lo RETIRA en {nmove} de {n} ({100*nmove/n:.0f}%) · "
                  f"p10 {1000*q(MOVE,.10):.0f} ms · mediana {1000*med(MOVE):.0f} · p90 {1000*q(MOVE,.90):.0f}")
        print(f"  no pasa ninguna de las dos en {HOR:.0f}s: {nsurv} casos ({100*nsurv/n:.0f}%)")
        if WIN:
            print(f"\n  VENTANA DE OPORTUNIDAD (lo primero que ocurra): p10 {1000*q(WIN,.10):.0f} ms · "
                  f"mediana {1000*med(WIN):.0f} · p90 {1000*q(WIN,.90):.0f}")
        print(f"\n  {'a los':>10}{'sigue viva':>14}   perfil")
        for M in MARKS:
            alive = nsurv + sum(1 for x in WIN if x > M)
            frac = alive / n
            tag = "  ← lo nuestro (77 ms)" if abs(M - OURS) < 1e-9 else ""
            print(f"  {1000*M:>7.0f} ms{100*frac:>13.0f}%   " + "█" * int(round(40 * frac)) + tag)

    print("\nLECTURA: la fila de 77 ms es la que decide. Si a esa altura sigue viva la mayoría, llegamos y el")
    print("mecanismo es nuestro; el resto es ingeniería. Si a 77 ms ya ha desaparecido la mayor parte,")
    print("competimos contra gente dentro del centro de datos y correr más no arregla nada.")
    print("Ojo a la descomposición: que la LEVANTE otro y que el maker la RETIRE son cosas distintas. Si casi")
    print("siempre la retira el maker, no hay competencia por la orden, solo un maker espabilado — y entonces")
    print("lo que importa es llegar antes que él, no antes que otros takers.")


if __name__ == "__main__":
    try: sys.stdout.reconfigure(encoding="utf-8")
    except Exception: pass
    main()
