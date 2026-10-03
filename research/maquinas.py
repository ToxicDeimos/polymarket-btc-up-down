"""
maquinas.py — ¿CUESTA LO MISMO MANDAR UNA ORDEN DESDE LA PI QUE DESDE EL PC?

Importa por dos motivos. Uno práctico: si una máquina es claramente más lenta, hay que operar desde la
otra. Y otro estadístico, que es el que más: si los `ms_envio` son parecidos, las dos series de órdenes
miden lo mismo y se pueden **sumar** para estimar el relleno; si no, son instrumentos distintos y
juntarlas sería el sesgo de siempre.

Compara los registros que haya en este directorio: `stalebot_log.csv` (la máquina actual) contra
cualquier `stalebot_log_*.csv` archivado de otra.

⚠ La comparación solo vale a partir del arreglo de las cachés del SDK (2-oct 18:25), que quitó ~80 ms
del envío. Mezclar órdenes de antes y después compararía el arreglo, no las máquinas.

    cd ~/polymarket-btc-up-down/research && python3 maquinas.py
"""
import csv, glob, os, sys, time, statistics as st

DIR = os.path.dirname(os.path.abspath(__file__))
# El arreglo de las caches entro aqui; antes de esto el envio costaba ~80 ms mas por otra razon.
CACHES = time.mktime(time.strptime("2026-10-02 18:25", "%Y-%m-%d %H:%M"))


def carga(p):
    try:
        R = [r for r in csv.DictReader(open(p, encoding="utf-8", errors="replace"))
             if (r.get("modo") or "") == "real"]
    except Exception:
        return []
    out = []
    for r in R:
        try: t = float(r["ts"])
        except Exception: continue
        if t < CACHES: continue                       # solo tras el arreglo de las caches
        try: ms = float(r["ms_envio"])
        except Exception: ms = None
        out.append((t, ms, (r.get("estado") or "")))
    return out


def resumen(nom, v):
    if not v:
        print(f"  {nom:<34} sin ordenes tras el arreglo de las caches"); return None
    ms = sorted(x[1] for x in v if x[1] is not None)
    lle = sum(1 for x in v if x[2] == "matched")
    p = lle / len(v)
    e = (p * (1 - p) / len(v)) ** 0.5
    desde = time.strftime("%d-%b %H:%M", time.localtime(min(x[0] for x in v)))
    hasta = time.strftime("%d-%b %H:%M", time.localtime(max(x[0] for x in v)))
    print(f"  {nom:<34} {desde} → {hasta}")
    if ms:
        print(f"     ms_envio  mediana {st.median(ms):>5.0f} · p25 {ms[len(ms)//4]:>4.0f} · "
              f"p75 {ms[3*len(ms)//4]:>4.0f} · n={len(ms)}")
    print(f"     relleno   {100*p:>4.0f}% ± {100*e:<4.0f} ({lle}/{len(v)})")
    return ms


def main():
    series = {}
    aqui = os.path.join(DIR, "stalebot_log.csv")
    if os.path.exists(aqui): series["esta maquina (stalebot_log.csv)"] = carga(aqui)
    for p in sorted(glob.glob(os.path.join(DIR, "stalebot_log_*.csv"))):
        b = os.path.basename(p)
        if b[len("stalebot_log_"):].rstrip(".csv").isdigit(): continue   # rotaciones por fecha
        series[b] = carga(p)
    if len(series) < 2:
        print("hace falta al menos otro stalebot_log_<maquina>.csv para comparar"); return

    print("solo ordenes POSTERIORES al arreglo de las caches del SDK (2-oct 18:25)\n")
    todos = {}
    for nom, v in series.items():
        ms = resumen(nom, v)
        if ms: todos[nom] = ms
        print()

    if len(todos) == 2:
        (na, a), (nb, b) = todos.items()
        d = st.median(a) - st.median(b)
        # Mann-Whitney aproximado: ¿se solapan las distribuciones o no?
        gan = sum(1 for x in a for y in b if x > y) / (len(a) * len(b))
        print("=" * 70)
        print(f"  diferencia de medianas: {d:+.0f} ms")
        print(f"  una orden de «{na.split(' ')[0]}» es mas lenta que una de «{nb.split(' ')[0]}» "
              f"el {100*gan:.0f}% de las veces")
        print("  (50% = indistinguibles · <35% o >65% = una maquina es claramente distinta)")
        if 0.35 < gan < 0.65:
            print("\n  ⇒ comparables: las dos series miden lo mismo y se pueden SUMAR")
        else:
            print("\n  ⇒ NO comparables: son instrumentos distintos, analizar por separado")


if __name__ == "__main__":
    try: sys.stdout.reconfigure(encoding="utf-8")
    except Exception: pass
    main()
