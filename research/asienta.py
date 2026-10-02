"""
asienta.py — ¿A LOS 8 SEGUNDOS EL LIBRO YA HA TERMINADO DE RECOTIZAR?

`fillsel` dejó una contradicción que no se puede dejar pasar:

    mecanismo (markout contra el medio a 8 s)  +3,29 ± 0,18
    aguantar a resolución                      +8,61 ± 0,68

Según nuestra propia lógica esas dos cifras miden LO MISMO. El argumento era: el mercado está calibrado
(20 pruebas), así que el medio del libro es un pronóstico INSESGADO del resultado y el markout contra él
es el mismo número que aguantar, con 4× menos ruido. Pues están a 7,6 errores típicos. Falla algo.

Dos candidatos, y llevan a sitios opuestos:

  (A) A los 8 s el libro TODAVÍA no ha terminado. Entonces mid8s no es "el pronóstico asentado", el
      markout se queda corto y la cifra buena es la de resolución. El mecanismo sería un SUELO.
  (B) El emparejamiento ventana→resultado tiene un fallo y el +8,61 es humo. El control espejo
      (−14,35 contra +10,77) juega en contra de esta, pero no la descarta.

Se distinguen mirando la trayectoria del medio. Si sigue subiendo a 15, 30, 60 y 120 s, es (A).
Si se queda plano desde los 8 s y el resultado sigue 5pp por encima, es (B) o una miscalibración
condicional de 5pp, que en un mercado líquido es una afirmación MUY gorda y habría que auditarla.

Todos los plazos se comparan sobre EL MISMO subconjunto de disparos (los que tienen los cuatro), porque
si no, la trayectoria mezcla horizontes con poblaciones distintas y no dice nada.

    cd ~/polymarket-btc-up-down/research && python3 asienta.py
"""
import csv, os, sys, glob, statistics as st

DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, DIR)
from stalepaper import fee, _resol, TRIGID, TARDE, LOG_TARDE      # noqa: E402

MAX_ACT = 102


def num(v):
    try:
        f = float(v)
        return f if 0 < f < 1 else None
    except Exception:
        return None


def stat(v):
    if len(v) < 20: return None
    return 100 * st.mean(v), 100 * st.pstdev(v) / (len(v) ** 0.5)


def fmt(s):
    return f"{s[0]:+.2f} ± {s[1]:.2f}" if s else "—"


def actividad(filas):
    filas.sort(key=lambda f: f["t"])
    ini, j = filas[0]["t"], 0
    for i, f in enumerate(filas):
        f["act"] = None
        if i and f["t"] - filas[i - 1]["t"] > 1800: ini = f["t"]; continue
        while filas[j]["t"] < f["t"] - 3600: j += 1
        if f["t"] - ini >= 3600: f["act"] = i - j


def main():
    if not os.path.exists(LOG_TARDE):
        print("todavía no hay stalepaper_tarde.csv — hace falta que stalepaper corra un rato con la")
        print("versión nueva. Con unas horas basta para ver la forma de la curva.")
        return

    tarde = {}
    for p in sorted(glob.glob(os.path.join(DIR, "stalepaper_tarde*.csv"))):
        for r in csv.DictReader(open(p, encoding="utf-8")):
            if r.get("trig") != TRIGID: continue
            tarde[r["ts_salto"]] = r          # las versiones viejas no traen spot: se usan igual en (a) y (b)

    R = []
    for p in sorted(glob.glob(os.path.join(DIR, "stalepaper*.csv"))):
        # excluir por NOMBRE, no solo el LOG_TARDE exacto: al rotar la cabecera quedan
        # stalepaper_tarde_2026....csv, que casan con el glob igual.
        if "_tarde" in os.path.basename(p): continue
        with open(p, encoding="utf-8") as fh: R.extend(csv.DictReader(fh))
    R = [r for r in R if r.get("trig") == TRIGID]
    print(f"disparos del disparador actual: {len(R)} · con plazos largos: {len(tarde)}")

    RES = _resol({int(r["ws"]) for r in R if r.get("ws")})
    filas = []
    for r in R:
        a, m8 = num(r.get("ask52")), num(r.get("mid8s"))
        if a is None or m8 is None: continue
        try: t = float(r["ts_salto"])
        except Exception: continue
        w = RES.get(int(r["ws"])) if r.get("ws") else None
        filas.append({"t": t, "a": a, "m8": m8, "tok": r.get("tok"), "tr": tarde.get(r["ts_salto"]),
                      "won": None if w is None else (1.0 if w == r.get("tok") else 0.0)})
    actividad(filas)

    # mismo subconjunto para todos los plazos: los que tienen los cuatro y resolución
    cols = [f"mid{int(h)}s" for h in TARDE]
    comp = []
    for f in filas:
        if not f["tr"] or f["won"] is None: continue
        v = [num(f["tr"].get(c)) for c in cols]
        if any(x is None for x in v): continue
        f["mids"] = v
        def spot(k):
            try: return float(f["tr"].get(k) or 0) or None
            except Exception: return None
        f["s0"] = spot("spot0")
        f["spots"] = [spot(f"spot{int(h)}s") for h in TARDE]
        comp.append(f)
    print(f"disparos con los cuatro plazos Y resolución: {len(comp)}")
    if len(comp) < 100:
        print("\nmuestra corta todavía — dejar correr stalepaper unas horas más y repetir.")
        print("(los plazos largos solo se graban si la ventana tiene tiempo por delante, así que")
        print(" se llenan más despacio que el fichero principal)")
        if not comp: return

    def curva(v, titulo):
        print("\n" + "=" * 78)
        print(f"  {titulo}   (n = {len(v)})")
        print("=" * 78)
        print(f"  {'plazo':>10}{'markout contra el ask pagado':>34}{'% del camino':>16}")
        base = [x["m8"] - x["a"] - fee(x["a"]) for x in v]
        fin = [x["won"] - x["a"] - fee(x["a"]) for x in v]
        sf = stat(fin)
        s8 = stat(base)
        print(f"  {'8 s':>10}{fmt(s8):>34}{(100*s8[0]/sf[0] if sf and sf[0] else 0):>15.0f}%")
        for i, h in enumerate(TARDE):
            s = stat([x["mids"][i] - x["a"] - fee(x["a"]) for x in v])
            print(f"  {f'{int(h)} s':>10}{fmt(s):>34}"
                  f"{(100*s[0]/sf[0] if sf and sf[0] and s else 0):>15.0f}%")
        print(f"  {'RESOLUCIÓN':>10}{fmt(sf):>34}{100:>15.0f}%")

    def pareja(v, titulo):
        """Diferencias EMPAREJADAS: mismo disparo medido en dos momentos. Las barras de la tabla de
        arriba son sin emparejar y exageran el ruido -- mid8 y mid120 estan correlacionadisimos, asi
        que la resta tiene mucha menos varianza que la suma de sus barras."""
        print("\n" + "=" * 78)
        print(f"  {titulo}   (n = {len(v)})")
        print("=" * 78)
        print("  a) ¿sigue moviéndose el medio DESPUÉS de los 8 s?   (mid_plazo − mid_8s)")
        print(f"  {'plazo':>10}{'se mueve':>22}{'z':>8}")
        for i, h in enumerate(TARDE):
            d = [x["mids"][i] - x["m8"] for x in v]
            s = stat(d)
            if not s: continue
            print(f"  {f'{int(h)} s':>10}{fmt(s):>22}{s[0]/s[1]:>8.1f}")
        print("\n  b) ¿queda algo por cobrar MÁS ALLÁ de cada plazo?   (resultado − mid_plazo)")
        print(f"  {'plazo':>10}{'queda':>22}{'z':>8}")
        s = stat([x["won"] - x["m8"] for x in v])
        print(f"  {'8 s':>10}{fmt(s):>22}{s[0]/s[1]:>8.1f}")
        for i, h in enumerate(TARDE):
            d = [x["won"] - x["mids"][i] for x in v]
            s = stat(d)
            if not s: continue
            print(f"  {f'{int(h)} s':>10}{fmt(s):>22}{s[0]/s[1]:>8.1f}")
        print("\n  (b) tiene un suelo de ruido irreducible: el resultado es binario, así que su error")
        print("      típico no baja de ~1,5pp con esta n por mucho que emparejemos. (a) no: ahí el")
        print("      emparejamiento sí muerde y es donde está la respuesta.")

    def descontando_btc(v):
        """El ruido que ahoga (a) no es de medicion: entre los 8 s y los 120 s BTC se mueve de verdad y
        el medio le sigue. Eso NO es recotizar tarde, es informacion nueva. Se descuenta ajustando
            (mid_h - mid_8) = alfa + beta * (spot_h - spot_8)
        por minimos cuadrados; alfa es la deriva que BTC NO explica, que es la que buscamos."""
        print("\n" + "=" * 78)
        print("  DESCONTANDO EL MOVIMIENTO DE BTC   (α = deriva que BTC no explica)")
        print("=" * 78)
        con = [x for x in v if x.get("s0") is not None and all(s is not None for s in x["spots"])]
        print(f"  disparos con spot guardado: {len(con)} de {len(v)}")
        if len(con) < 150:
            print("  aún no hay bastantes con spot — es la versión nueva de stalepaper, hace falta que")
            print("  corra unas horas. Hasta entonces manda la tabla emparejada de arriba.")
            return
        print(f"  {'plazo':>10}{'α (sin explicar)':>24}{'z':>7}{'β ($/pp)':>12}{'ruido quitado':>16}")
        for i, h in enumerate(TARDE):
            # signo: compramos 'tok', asi que si es Down una BAJADA de BTC nos favorece
            xs = [(1 if x["tok"] == "Up" else -1) * (x["spots"][i] - x["s0"]) for x in con]
            ys = [x["mids"][i] - x["m8"] for x in con]
            n = len(xs); mx = sum(xs) / n; my = sum(ys) / n
            sxx = sum((a - mx) ** 2 for a in xs)
            if sxx <= 0: continue
            beta = sum((a - mx) * (b - my) for a, b in zip(xs, ys)) / sxx
            alfa = my - beta * mx
            res = [b - (alfa + beta * a) for a, b in zip(xs, ys)]
            # error tipico de alfa en una regresion simple
            s2 = sum(r * r for r in res) / max(n - 2, 1)
            se = (s2 * (1.0 / n + mx * mx / sxx)) ** 0.5
            crudo = stat(ys)
            print(f"  {f'{int(h)} s':>10}{100*alfa:>+16.2f} ± {100*se:<5.2f}{alfa/se if se else 0:>7.1f}"
                  f"{beta*100:>11.3f}{f'{100*(1 - se/(crudo[1]/100)):.0f}%' if crudo else '—':>16}")
        print("\n  β dice cuánto se mueve el medio (en pp) por cada dólar de BTC a nuestro favor: es la")
        print("  sensibilidad del mercado, y sirve de comprobación — tiene que salir positiva.")
        print("  α es la respuesta: si es > 0 con z alto, el libro seguía recotizando a los 8 s.")

    curva(comp, "TRAYECTORIA DEL MEDIO DESPUÉS DE COMPRAR")
    pareja(comp, "LO MISMO, EMPAREJADO")
    descontando_btc(comp)
    tr = [f for f in comp if f["act"] is not None and f["act"] <= MAX_ACT]
    if len(tr) >= 100:
        curva(tr, f"SOLO RÉGIMEN TRANQUILO (≤{MAX_ACT}/h causal)")

    print("\n" + "=" * 78)
    print("  CÓMO SE LEE")
    print("=" * 78)
    print("  · Si la columna del % crece de 8 s a 120 s, el libro seguía recotizando y el mecanismo era")
    print("    un SUELO: la cifra honesta de aguantar es la de resolución.")
    print("  · Si a los 8 s ya está en el ~100% y la resolución se va muy por encima, entonces el medio")
    print("    asentado NO es un pronóstico insesgado aquí y hay que auditar el cruce ventana→resultado")
    print("    antes de creerse el +8,61.")
    print("  · ⚠ Estos disparos son los que tenían 133 s de ventana por delante, así que están sesgados")
    print("    hacia el principio de la ventana. La TRAYECTORIA es válida (mismo subconjunto en todos")
    print("    los plazos); el NIVEL no es comparable con el de la muestra entera.")


if __name__ == "__main__":
    try: sys.stdout.reconfigure(encoding="utf-8")
    except Exception: pass
    main()
