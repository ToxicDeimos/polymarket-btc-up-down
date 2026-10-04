"""
botlog.py — ¿QUÉ ESTÁ PASANDO CON LAS ÓRDENES REALES?

La sesión en vivo escupe líneas sueltas por pantalla y es fácil quedarse con la impresión en vez del
número. Esto lee `stalebot_log.csv` y contesta las cuatro preguntas que importan ahora mismo:

  A) el marcador: de cada intento, ¿se llena, lo matan, o falla de otra forma?
  B) el ms_envio: cuánto tarda de verdad, y si CALENTAR LA CONEXIÓN cambió algo
  C) ¿el relleno depende de la actividad, de la profundidad del ask o del tiempo que queda?
  D) la respuesta cruda del primer relleno — los nombres de los campos siguen siendo una suposición,
     y hasta verlos el contador de gasto va a ciegas asumiendo lo peor

⚠ Con pocas órdenes esto NO decide nada. Una tasa de relleno con n=5 tiene un error de ±22 puntos.
   Se imprime el intervalo para que se vea, y por debajo de 15 intentos no se saca conclusión.

    cd ~/polymarket-btc-up-down/research && python3 botlog.py
"""
import csv, glob, os, sys, time, statistics as st

DIR = os.path.dirname(os.path.abspath(__file__))
LOG = os.path.join(DIR, "stalebot_log.csv")
# el calentamiento de conexion entro en el commit 09a8c37, la tarde del 2-oct
CALIENTE = 1790935200


def pct(k, n):
    """proporcion con su error (Wald, suficiente aqui) — la n pequeña tiene que VERSE"""
    if not n: return "—"
    p = k / n
    return f"{100*p:.0f}% ± {100*(p*(1-p)/n)**0.5:.0f}  ({k}/{n})"


def carga():
    """Todas las ordenes reales, incluidas las de los registros APARTADOS.

    Cuando cambian las columnas, stalebot renombra el registro a stalebot_log_<fecha>.csv y empieza
    uno nuevo (no borra nada). Leyendo solo el principal perderiamos de vista todo lo anterior sin
    avisar — que es justo lo que paso al añadir la columna 'retraso' del A/B.
    """
    R = []
    for p in sorted(glob.glob(os.path.join(DIR, "stalebot_log_*.csv"))) + [LOG]:
        if not os.path.exists(p): continue
        for r in csv.DictReader(open(p, encoding="utf-8", errors="replace")):
            if (r.get("modo") or "") != "real": continue
            try: r["_t"] = float(r["ts"])
            except Exception: continue
            R.append(r)
    return R


def clase(r):
    e = (r.get("estado") or "").lower()
    if e == "matada": return "matada"
    if e == "excepcion": return "error"
    try:
        if float(r.get("size_llenado") or 0) > 0: return "llena"
    except Exception: pass
    return "sin relleno" if e else "?"


def main():
    R = carga()
    if not R:
        print("todavía no hay órdenes reales en el registro."); return
    R.sort(key=lambda r: r["_t"])
    print(f"órdenes reales: {len(R)} · "
          f"{time.strftime('%d-%b %H:%M', time.localtime(R[0]['_t']))} → "
          f"{time.strftime('%H:%M', time.localtime(R[-1]['_t']))}")

    print("\n" + "=" * 72)
    print("  A) MARCADOR")
    print("=" * 72)
    cl = [clase(r) for r in R]
    for k in ("llena", "matada", "error", "sin relleno", "?"):
        n = cl.count(k)
        if n: print(f"  {k:>14}  {n:>4}   {100*n/len(R):>4.0f}%")
    llenas = cl.count("llena")
    print(f"\n  TASA DE RELLENO: {pct(llenas, len(R))}")
    if len(R) < 15:
        print("  ⚠ menos de 15 intentos: no se puede concluir nada todavía.")

    print("\n" + "=" * 72)
    print("  B) ms_envio  ·  ¿sirvió calentar la conexión?")
    print("=" * 72)
    for nom, sel in (("antes de calentar", lambda r: r["_t"] < CALIENTE),
                     ("con conexión caliente", lambda r: r["_t"] >= CALIENTE)):
        v = []
        for r in R:
            if not sel(r): continue
            try: v.append(float(r["ms_envio"]))
            except Exception: pass
        if not v: print(f"  {nom:>22}  —"); continue
        v.sort()
        print(f"  {nom:>22}  mediana {st.median(v):>5.0f} ms · min {v[0]:.0f} · máx {v[-1]:.0f}  (n={len(v)})")
    print("\n  referencia medida aparte: red caliente 61 ms · saludo TCP+TLS 74 ms")

    print("\n" + "=" * 72)
    print("  C) ¿DE QUÉ DEPENDE QUE SE LLENE?")
    print("=" * 72)

    def corta(nombre, clave, cortes):
        print(f"\n  por {nombre}:")
        for et, lo, hi in cortes:
            sub = []
            for r in R:
                try: x = float(r[clave])
                except Exception: continue
                if lo <= x < hi: sub.append(clase(r) == "llena")
            if sub: print(f"    {et:>16}  {pct(sum(sub), len(sub))}")

    corta("actividad (det/h)", "actividad", [("tranquilo ≤104", 0, 105), ("medio 105-193", 105, 194),
                                             ("agitado >193", 194, 1e9)])
    corta("acciones en el ask", "tam_visto", [("<20", 0, 20), ("20-100", 20, 100), (">100", 100, 1e9)])
    corta("segundos de ventana", "ttc", [("60-120", 60, 120), ("120-200", 120, 200), (">200", 200, 1e9)])

    print("\n" + "=" * 72)
    print("  D) PRIMERA RESPUESTA CRUDA DE UNA ORDEN QUE SE LLENÓ")
    print("=" * 72)
    prim = next((r for r in R if clase(r) == "llena"), None)
    if not prim:
        print("  ninguna todavía. Es lo que falta para dejar de adivinar los nombres de los campos")
        print("  (takingAmount / size_matched / sizeMatched) y que el contador de gasto deje de ir a ciegas.")
    else:
        print(f"  {time.strftime('%d-%b %H:%M:%S', time.localtime(prim['_t']))} · {prim['tok']} a {prim['ask_visto']}")
        for k in ("estado", "size_llenado", "precio_medio", "order_id", "ms_envio"):
            print(f"    {k:<14} {prim.get(k)}")
        print(f"    respuesta_cruda  {str(prim.get('respuesta_cruda'))[:300]}")

    ab(R)
    realizado(R)


def ab(R):
    """F) EL A/B DE LATENCIA: ¿nos llena poco porque llegamos tarde o porque perdemos la cola?

    Las dos medidas que teniamos se contradicen (el papel dice que el relleno cae del 92% al 61%
    entre 52 y 200 ms; el PC es 55 ms mas rapido que la Pi y llena igual) y ninguna tenia potencia.
    El bot alterna ahora ventanas PARES sin retraso e IMPARES con un retraso deliberado, asi que la
    unica diferencia entre las dos ramas es CUANDO llega la orden.

    El retraso es grande (250 ms por defecto) a proposito: es mucho mas de lo que podriamos ganar
    acercandonos al origen de Polymarket, asi que si ESTO no mueve el relleno, lo pequeño tampoco.
    """
    import statistics as st
    print("\n" + "=" * 72)
    print("  F) A/B DE LATENCIA — ¿la velocidad cambia el relleno?")
    print("=" * 72)
    ramas = {}
    for r in R:
        # 🐛 Las ordenes ANTERIORES al A/B no tienen columna 'retraso'. Tratarlas como 0 las metia
        # en la rama rapida y la comparacion salia 361 contra 9: no estabamos comparando ramas,
        # estabamos comparando el historico entero contra las nueve de hoy.
        v = (r.get("retraso") or "").strip()
        if not v: continue
        try: d = int(float(v))
        except Exception: continue
        ramas.setdefault(d, []).append(r)
    if not ramas or (len(ramas) == 1 and 0 in ramas):
        print("  todavia no hay ordenes con el A/B activo (columna 'retraso' vacia o toda a 0).")
        print("  Las ordenes anteriores al 4-oct-2026 no lo llevan y no entran en esta comparacion.")
        return
    print(f"  {'rama':<18}{'ordenes':>9}{'relleno':>22}{'ms_envio':>11}")
    datos = {}
    for d in sorted(ramas):
        g = ramas[d]
        k = sum(1 for r in g if clase(r) == "llena")
        ms = [float(r["ms_envio"]) for r in g if (r.get("ms_envio") or "").strip()]
        datos[d] = (k, len(g))
        nom = "rapida" if d == 0 else f"lenta (+{d} ms)"
        print(f"  {nom:<18}{len(g):>9}{pct(k, len(g)):>22}"
              f"{(f'{st.median(ms):.0f}' if ms else '—'):>11}")

    if len(datos) >= 2 and 0 in datos:
        d1 = max(x for x in datos if x > 0)
        k0, n0 = datos[0]; k1, n1 = datos[d1]
        if n0 and n1:
            p0, p1 = k0 / n0, k1 / n1
            se = (p0 * (1 - p0) / n0 + p1 * (1 - p1) / n1) ** 0.5
            print(f"\n  diferencia (rapida − lenta): {100*(p0-p1):+.1f} puntos"
                  f" ± {100*se:.1f}   z = {((p0-p1)/se) if se else float('nan'):+.2f}")
            # potencia: ¿podria este tamaño de muestra ver una caida a la MITAD?
            p = (k0 + k1) / (n0 + n1)
            if 0 < p < 1:
                nmin = int(1.96 ** 2 * 2 * p * (1 - p) / (p / 2) ** 2) + 1
                print(f"  para distinguir 'se llena la mitad' hacen falta ~{nmin} ordenes por rama"
                      f" (hay {min(n0, n1)})")
            if se and abs(p0 - p1) > 2 * se:
                print("\n  ⇒ LA VELOCIDAD SI MANDA. Merece la pena medir cuanto se gana acercandose")
                print("     al origen de Polymarket (un VPS europeo ahorra ~30 ms de red).")
            elif min(n0, n1) >= (nmin if 0 < p < 1 else 1e9):
                print("\n  ⇒ CON MUESTRA SUFICIENTE, 250 ms NO CAMBIAN EL RELLENO. No es velocidad:")
                print("     es la cola. Ningun VPS ni optimizacion de codigo arregla eso.")
            else:
                print("\n  (aun sin potencia para concluir: dejar correr)")


def realizado(R):
    """E) EL MARGEN REALIZADO: lo único que decide si esto paga.

    Ni el relleno ni la latencia contestan la pregunta. Un 7% de relleno con margen bueno es un
    negocio; un 80% con margen cero no lo es. Y no vale apoyarse en `fillsel` para estimarlo: su
    modelo predecía 35-40% de relleno donde la realidad da 7%, así que si falla en QUIÉN se llena,
    también falla en CUÁNTO vale lo que se llena.

    Esto lo mide de la única forma que no admite discusión: con el resultado de nuestras compras.
    """
    import re
    print("\n" + "=" * 72)
    print("  E) MARGEN REALIZADO DE LAS COMPRAS")
    print("=" * 72)
    lle = [r for r in R if (r.get("estado") or "") == "matched" and r.get("cid")]
    sincid = sum(1 for r in R if (r.get("estado") or "") == "matched" and not r.get("cid"))
    if sincid:
        print(f"  ⚠ {sincid} rellenos SIN conditionId (anteriores al arreglo): no se pueden resolver.")
    if not lle:
        print("  todavía no hay rellenos con cid. Cada uno tarda ≤5 min en resolverse.")
        print("  Con ~7 al día, en dos semanas son ~100, que dan el margen a ±5 puntos.")
        return
    try:
        from stalepaper import get, fee
    except Exception:
        print("  no se pudo importar stalepaper"); return
    net, apost, gan = [], 0.0, 0
    for r in lle:
        d = get(f"https://clob.polymarket.com/markets/{r['cid']}") or {}
        w = next((t.get("outcome") for t in d.get("tokens", []) if t.get("winner") is True), None)
        if w is None: continue
        m = re.search(r"'makingAmount': '([\d.]+)'", r.get("respuesta_cruda") or "")
        try:
            n = float(r["size_llenado"]); pag = float(m.group(1)) if m else n * float(r["ask_visto"])
        except Exception: continue
        cobro = n if w == r["tok"] else 0.0
        net.append((cobro - pag) / pag)          # retorno sobre lo apostado
        apost += pag; gan += (w == r["tok"])
    if len(net) < 5:
        print(f"  solo {len(net)} resueltas, aún no dice nada"); return
    import statistics as st
    m = st.mean(net); e = st.pstdev(net) / len(net) ** 0.5
    print(f"  {len(net)} compras resueltas · ganadas {gan} ({100*gan/len(net):.0f}%)")
    print(f"  apostado {apost:.2f}$ · resultado neto {sum(n*apost/len(net) for n in net):+.2f}$")
    print(f"\n  MARGEN POR OPERACIÓN: {100*m:+.2f}% ± {100*e:.2f}")
    print(f"  (la hipótesis 'margen cero' {'NO se descarta' if abs(m) < 2*e else 'SE DESCARTA'})")
    if e > 0:
        falta = int(len(net) * (e / 0.025) ** 2) - len(net)
        if falta > 0:
            print(f"  para bajar el error a ±2,5 puntos faltan ~{falta} operaciones "
                  f"({falta/7.4:.0f} días al ritmo actual)")


if __name__ == "__main__":
    try: sys.stdout.reconfigure(encoding="utf-8")
    except Exception: pass
    main()
