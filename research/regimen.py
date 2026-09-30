"""
regimen.py — ¿DE QUÉ DEPENDE QUE EL EDGE SEA GRANDE O PEQUEÑO?

El análisis del papel dejó un hecho nuevo al llegar al cuarto día: el mecanismo NO es constante.
      24-sep +1,24 · 25-sep +0,89 · 26-sep +5,05 · 27-sep +5,14
Cinco veces de diferencia. La resolución acompaña (+4,56 / +5,88 / +9,88 / +6,86) pero con menos rango.
Supuse que era la volatilidad de BTC —días movidos, más retraso del libro, más margen— y no lo he medido.

Con 4-6 días no hay muestra para correlacionar nada. Por HORA sí: ~140 horas con decenas de disparos cada
una. Y se usa un indicador que ya tenemos y que se conoce EN VIVO: **cuántas veces dispara la señal por
hora**. Más disparos = BTC moviéndose más. No hace falta cruzar con nada externo.

Si el edge crece con la actividad, la consecuencia es práctica y directa: encender el bot solo en las horas
activas mejora el resultado por operación Y reduce cuántas órdenes hacen falta para medir algo — que con el
saldo contado es justo lo que interesa.

  A) por hora del día (UTC) — ¿hay un patrón de sesión?
  B) por ACTIVIDAD (disparos/hora, en cuartiles) — el que se puede usar como filtro en vivo
  C) si con eso se filtrara, cuánto quedaría: operaciones perdidas frente a edge ganado

    cd ~/polymarket-btc-up-down/research && python3 regimen.py
"""
import csv, os, sys, glob, time, statistics as st

DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, DIR)
from stalepaper import fee, _resol, TRIGID      # noqa: E402  (mismo criterio que el analisis del papel)


def carga():
    R = []
    for p in sorted(glob.glob(os.path.join(DIR, "stalepaper*.csv"))):
        with open(p, encoding="utf-8") as fh: R.extend(csv.DictReader(fh))
    return [r for r in R if r.get("trig") == TRIGID]


def stat(v):
    if len(v) < 20: return None
    return 100 * st.mean(v), 100 * st.pstdev(v) / (len(v) ** 0.5)


def fmt(s):
    return f"{s[0]:+.2f} ± {s[1]:.2f}" if s else "—"


def main():
    R = carga()
    print(f"disparos del disparador actual: {len(R)}")
    if len(R) < 500:
        print("muestra corta — dejar acumular"); return
    RES = _resol({int(r["ws"]) for r in R if r.get("ws")})

    filas = []
    for r in R:
        try:
            t = float(r["ts_salto"]); a = float(r["ask52"]); m = float(r["mid8s"])
        except Exception: continue
        if not (0 < a < 1 and 0 < m < 1): continue
        w = RES.get(int(r["ws"])) if r.get("ws") else None
        won = None if w is None else (1.0 if w == r.get("tok") else 0.0)
        filas.append({"t": t, "hora": time.strftime("%Y-%m-%d %H", time.gmtime(t)),
                      "hd": int(time.strftime("%H", time.gmtime(t))),
                      "mec": m - a - fee(a),
                      "res": None if won is None else won - a - fee(a)})
    print(f"utilizables: {len(filas)}")

    # ---------- A) hora del dia ----------
    print("\n" + "=" * 76)
    print("  A) POR HORA DEL DÍA (UTC)")
    print("=" * 76)
    print(f"  {'hora':>6}{'n':>8}{'MECANISMO':>18}{'A RESOLUCIÓN':>20}")
    porhd = {}
    for f in filas: porhd.setdefault(f["hd"], []).append(f)
    for h in sorted(porhd):
        v = porhd[h]
        s = stat([x["mec"] for x in v])
        rr = [x["res"] for x in v if x["res"] is not None]
        print(f"  {h:>4}h{len(v):>8}{fmt(s):>18}{fmt(stat(rr)):>20}")

    # ---------- B) actividad ----------
    print("\n" + "=" * 76)
    print("  B) POR ACTIVIDAD — disparos en esa hora concreta (se conoce EN VIVO)")
    print("=" * 76)
    porh = {}
    for f in filas: porh.setdefault(f["hora"], []).append(f)
    tam = sorted(len(v) for v in porh.values())
    if len(tam) < 8: print("  pocas horas todavía"); return
    q = [tam[int(len(tam) * p)] for p in (0.25, 0.50, 0.75)]
    print(f"  horas observadas: {len(tam)} · disparos/hora: mín {tam[0]} · "
          f"cuartiles {q[0]}/{q[1]}/{q[2]} · máx {tam[-1]}")
    print(f"  {'actividad':>22}{'horas':>7}{'n':>8}{'MECANISMO':>18}{'A RESOLUCIÓN':>20}")
    cortes = [("tranquila (≤Q1)", 0, q[0]), ("media-baja", q[0], q[1]),
              ("media-alta", q[1], q[2]), ("agitada (>Q3)", q[2], 10 ** 9)]
    for nm, lo, hi in cortes:
        hs = [h for h, v in porh.items() if lo < len(v) <= hi] if lo else \
             [h for h, v in porh.items() if len(v) <= hi]
        v = [f for h in hs for f in porh[h]]
        if len(v) < 20: print(f"  {nm:>22}{len(hs):>7}{len(v):>8}   (pocos)"); continue
        rr = [x["res"] for x in v if x["res"] is not None]
        print(f"  {nm:>22}{len(hs):>7}{len(v):>8}{fmt(stat([x['mec'] for x in v])):>18}"
              f"{fmt(stat(rr)):>20}")

    # ---------- C) qué costaría filtrar ----------
    print("\n" + "=" * 76)
    print("  C) SI SOLO OPERÁRAMOS EN LAS HORAS ACTIVAS")
    print("=" * 76)
    print(f"  {'umbral':>22}{'% de disparos':>16}{'MECANISMO':>18}{'A RESOLUCIÓN':>20}")
    for u in (q[0], q[1], q[2]):
        v = [f for h, vv in porh.items() if len(vv) > u for f in vv]
        if len(v) < 20: continue
        rr = [x["res"] for x in v if x["res"] is not None]
        print(f"  {f'>{u} disparos/hora':>22}{100*len(v)/len(filas):>15.0f}%"
              f"{fmt(stat([x['mec'] for x in v])):>18}{fmt(stat(rr)):>20}")
    # ---------- D) la misma actividad pero CAUSAL ----------
    # B y C agrupan por hora de RELOJ: un disparo de las 14:05 lleva la etiqueta de las 14:00-15:00
    # enteras, incluido lo que todavia no ha pasado. En vivo eso no se sabe. Aqui se cuenta solo lo
    # ANTERIOR: disparos en los 3600 s previos, que es exactamente lo que calcula el bot.
    print("\n" + "=" * 76)
    print("  D) ACTIVIDAD CAUSAL — disparos en los 60 min ANTERIORES (lo único que se sabe en vivo)")
    print("=" * 76)
    filas.sort(key=lambda f: f["t"])
    # Cortar en trozos: un hueco >1800 s sin un solo disparo es el grabador parado, no calma
    # (a 64/h, el cuartil mas tranquilo, media hora en blanco es practicamente imposible).
    ini, j, buenas = filas[0]["t"], 0, []
    for i, f in enumerate(filas):
        if i and f["t"] - filas[i - 1]["t"] > 1800: ini = f["t"]; continue
        while filas[j]["t"] < f["t"] - 3600: j += 1
        if f["t"] - ini >= 3600: f["act"] = i - j; buenas.append(f)
    print(f"  disparos con una hora limpia por detras: {len(buenas)} de {len(filas)}")
    if len(buenas) < 200:
        print("  muestra corta para esto — hacen falta tramos largos sin cortes")
    else:
        ac = sorted(f["act"] for f in buenas)
        qc = [ac[int(len(ac) * p)] for p in (0.25, 0.50, 0.75)]
        print(f"  disparos/hora causal: mín {ac[0]} · cuartiles {qc[0]}/{qc[1]}/{qc[2]} · máx {ac[-1]}")
        print(f"  {'actividad causal':>22}{'n':>8}{'MECANISMO':>18}{'A RESOLUCIÓN':>20}")
        for nm, lo, hi in [("tranquila (≤Q1)", -1, qc[0]), ("media-baja", qc[0], qc[1]),
                           ("media-alta", qc[1], qc[2]), ("agitada (>Q3)", qc[2], 10 ** 9)]:
            v = [f for f in buenas if lo < f["act"] <= hi]
            if len(v) < 20: print(f"  {nm:>22}{len(v):>8}   (pocos)"); continue
            rr = [x["res"] for x in v if x["res"] is not None]
            print(f"  {nm:>22}{len(v):>8}{fmt(stat([x['mec'] for x in v])):>18}{fmt(stat(rr)):>20}")
        print(f"\n  ⚠ el umbral que debe llevar el bot es el Q1 CAUSAL = {qc[0]}, no el de la hora de reloj.")
        print(f"     STALEBOT_MAX_ACT={qc[0]}")

    print("\nLECTURA: si el edge sube con la actividad y el filtro conserva buena parte de los disparos,")
    print("merece la pena encender el bot solo en esas horas: sube el resultado por operación Y hacen falta")
    print("menos órdenes para medir algo, que con el saldo contado es lo que interesa. Si sale plano, la")
    print("variación entre días es otra cosa y no hay filtro que aplicar — tampoco pasa nada, solo significa")
    print("que la sesión real hay que hacerla sin elegir momento.")


if __name__ == "__main__":
    try: sys.stdout.reconfigure(encoding="utf-8")
    except Exception: pass
    main()
