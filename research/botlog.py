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
import csv, os, sys, time, statistics as st

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
    if not os.path.exists(LOG): return []
    R = []
    for r in csv.DictReader(open(LOG, encoding="utf-8", errors="replace")):
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


if __name__ == "__main__":
    try: sys.stdout.reconfigure(encoding="utf-8")
    except Exception: pass
    main()
