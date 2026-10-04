"""
cruce.py — ¿TIENEN MARGEN LAS ÓRDENES QUE SÍ SE LLENAN?

Es la única pregunta que queda viva, y con dinero real no se puede contestar: harían falta ~200
rellenos para distinguir +7% de cero, o sea un mes y ~740 $ de volumen sobre una cuenta de 15 $.

Pero no hace falta esperar. Cada orden real del bot tiene su **gemela en el papel**: el mismo
disparo, la misma ventana, el mismo lado — y el papel lo mide con el markout continuo contra el
medio, no con una moneda al aire. Es la misma lección de siempre: el resultado binario tiene 4 veces
más ruido que el markout, y aquí lo necesitamos.

Así que se emparejan los intentos REALES (stalebot_log.csv) con las filas del PAPEL por ventana,
lado e instante, y se compara el margen de las que se llenaron contra el de las que murieron.

  · si las llenas tienen margen  → el problema es solo el 6% de relleno, y eso es de capacidad
  · si no lo tienen              → la selección adversa es real y la vía del taker está cerrada

⚠ El bot y el colector son procesos distintos con enfriamientos independientes, así que sus disparos
  no caen en el mismo milisegundo. Se empareja por ventana + lado + el disparo del papel más cercano
  dentro de ±4 s, y se descarta lo que no case. Los no emparejados se cuentan: si fueran muchos, la
  comparación estaría sesgada y habría que desconfiar.

    cd ~/polymarket-btc-up-down/research && python3 cruce.py
"""
import csv, glob, os, sys, statistics as st

DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, DIR)
from stalepaper import fee, TRIGID      # noqa: E402

VENTANA_EMPAREJE = 4.0


def num(v):
    try:
        f = float(v)
        return f if 0 < f < 1 else None
    except Exception:
        return None


def stat(v):
    if len(v) < 10: return None
    return 100 * st.mean(v), 100 * st.pstdev(v) / (len(v) ** 0.5)


def fmt(s):
    return f"{s[0]:+.2f} ± {s[1]:.2f}" if s else "—"


def main():
    # 1) el papel, indexado por (ventana, lado)
    papel = {}
    # Hay que mirar los DOS prefijos de 5m: el bot del PC tenia su papel en pcpaper_*, y el de la Pi
    # en stalepaper_*. Emparejar solo uno deja fuera la mitad de las ordenes (paso: 0 de 140).
    # ⛔ paper15 NO entra: sus ventanas son de 900 s y, al ser tambien multiplos de 300, un ws suyo
    # puede coincidir con el de una ventana de 5m y emparejar mal sin avisar.
    fuentes = sorted(glob.glob(os.path.join(DIR, "stalepaper*.csv")) +
                     glob.glob(os.path.join(DIR, "pcpaper*.csv")))
    for p in fuentes:
        if "_tarde" in os.path.basename(p): continue
        for r in csv.DictReader(open(p, encoding="utf-8", errors="replace")):
            if r.get("trig") != TRIGID: continue
            try: t, ws = float(r["ts_salto"]), int(r["ws"])
            except Exception: continue
            papel.setdefault((ws, r.get("tok")), []).append((t, r))
    print(f"papel: {sum(len(v) for v in papel.values())} disparos en {len(papel)} ventana×lado")

    # 2) las ordenes reales
    reales = []
    for p in [os.path.join(DIR, "stalebot_log.csv")] + sorted(
            glob.glob(os.path.join(DIR, "stalebot_log_*.csv"))):
        if not os.path.exists(p): continue
        for r in csv.DictReader(open(p, encoding="utf-8", errors="replace")):
            if (r.get("modo") or "") != "real": continue
            try: t, ws = float(r["ts"]), int(r["ws"])
            except Exception: continue
            reales.append((t, ws, r.get("tok"), (r.get("estado") or "") == "matched",
                           os.path.basename(p)))
    print(f"órdenes reales: {len(reales)}")
    if not reales: print("sin ordenes reales"); return

    # 3) emparejar
    pares, sin_papel, lejos, dist = [], 0, 0, []
    for t, ws, tok, lleno, orig in reales:
        cand = papel.get((ws, tok), [])
        if not cand: sin_papel += 1; continue
        mejor = min(cand, key=lambda x: abs(x[0] - t))
        d = abs(mejor[0] - t); dist.append(d)
        if d > VENTANA_EMPAREJE: lejos += 1; continue
        pares.append((lleno, mejor[1], orig))
    print(f"emparejados: {len(pares)}")
    # Separar las dos causas importa: si falta papel, el remedio es que corran a la vez; si es que
    # caen lejos, habria que repensar el emparejamiento. Medido en el PC: 101 sin papel y 1 lejos,
    # con mediana de distancia 0,0 s — los dos procesos disparan EN EL MISMO INSTANTE cuando ambos
    # estan vivos, asi que el metodo es bueno y lo que falla es la cobertura.
    print(f"  sin papel en esa ventana+lado: {sin_papel} ({100*sin_papel/len(reales):.0f}%)"
          f"  ·  con papel pero a mas de {VENTANA_EMPAREJE:.0f}s: {lejos}")
    if dist:
        dist.sort()
        print(f"  distancia al disparo mas cercano: mediana {dist[len(dist)//2]:.1f}s "
              f"· max {dist[-1]:.0f}s")
    if sin_papel > len(reales) * 0.4:
        print("⚠ falta papel en demasiadas: el colector no estaba grabando mientras el bot operaba.")
        print("  La comparacion estaria sesgada. Ejecutar donde ambos corran a la vez (la Pi).")
    if len(pares) < 40:
        print("muestra corta, dejar acumular"); return

    # 4) comparar el markout de llenas contra matadas
    print("\n" + "=" * 72)
    print("  MARGEN DEL PAPEL, SEGÚN SI LA ORDEN REAL SE LLENÓ O NO")
    print("=" * 72)
    print(f"  {'':<22}{'n':>6}{'MECANISMO (mid 8s)':>22}")
    grupos = {"se llenó": [p for p in pares if p[0]], "la mataron": [p for p in pares if not p[0]]}
    res = {}
    for nom, g in grupos.items():
        v = []
        for _, r, _ in g:
            a, m = num(r.get("ask52")), num(r.get("mid8s"))
            if a is None or m is None: continue
            v.append(m - a - fee(a))
        s = stat(v)
        res[nom] = s
        print(f"  {nom:<22}{len(v):>6}{fmt(s):>22}")

    a, b = res.get("se llenó"), res.get("la mataron")
    if a and b:
        d = a[0] - b[0]; e = (a[1] ** 2 + b[1] ** 2) ** 0.5
        print(f"\n  diferencia (llenas − matadas): {d:+.2f} ± {e:.2f}")
        print("\n" + "=" * 72)
        if a[0] > 1.0 and a[0] > 2 * a[1]:
            print("  ⇒ LAS QUE SE LLENAN TIENEN MARGEN. El problema es solo el 6% de relleno,")
            print("     que es un límite de capacidad, no de que la estrategia no funcione.")
        elif a[0] < 2 * a[1]:
            print("  ⇒ LAS QUE SE LLENAN NO TIENEN MARGEN DISTINGUIBLE DE CERO.")
            print("     Si ademas las matadas sí lo tienen, la seleccion adversa es real y")
            print("     la via del taker desde aqui esta cerrada.")
        print("=" * 72)
    print("\n  ⚠ Esto mide el MECANISMO (markout contra el medio a 8 s), que sabemos que es un SUELO:")
    print("  el libro sigue recotizando hasta los ~120 s (ver asienta.py), así que el margen real de")
    print("  aguantar es mayor que estas cifras. Sirve para COMPARAR los dos grupos, que es lo que")
    print("  se pregunta aquí, no como estimación del beneficio.")


if __name__ == "__main__":
    try: sys.stdout.reconfigure(encoding="utf-8")
    except Exception: pass
    main()
