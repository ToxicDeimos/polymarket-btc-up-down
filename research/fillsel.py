"""
fillsel.py — ¿EL SUBCONJUNTO QUE NOS LLENAN ES EL BUENO O EL MALO?

Todo lo medido hasta ahora supone que compramos. Pero la orden es FAK: si cuando llega el ask ya se ha
movido por encima de nuestro límite, no compramos NADA. Y ahí está la trampa:

    el ask se queda quieto justamente cuando el creador de mercado NO ha repreciado
    …que es exactamente de donde sale el margen.

Eso puede jugar a favor (nos llenan los casos buenos) o en contra (nos llenan solo cuando el movimiento
se dio la vuelta y no había nada que aprovechar). Es la misma pregunta que ya nos mordió una vez en
[[fill-maker-mal-medido]]: el relleno real era 22,7% y no 93,7%, y eso cambiaba el signo.

NO hace falta dinero para contestarla. El papel ya guarda el ask a 0/52/100/200 ms con su tamaño, así que
se puede reconstruir qué habría pasado:

    límite = el ask que VEMOS al decidir · llegada = 52/100/200 ms después · se llena si ask_llegada <= límite
    precio pagado = ask_llegada (un límite comprador se ejecuta al ask, nunca peor)

y comparar el margen de los que se llenan contra el de los que no. Si el margen vive en los que NO se
llenan, la estrategia está muerta y nos hemos ahorrado el viaje.

Se mira también pagar +1 tick (0,01), que sube el relleno pero cuesta ~1,8pp sobre un precio de 0,55 —
casi medio margen. Y todo repetido dentro del régimen tranquilo, que es donde el bot va a operar.

    cd ~/polymarket-btc-up-down/research && python3 fillsel.py
"""
import csv, os, sys, glob, statistics as st

DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, DIR)
from stalepaper import fee, _resol, TRIGID      # noqa: E402

TICK = 0.01
MAX_ACT = 102          # el primer cuartil causal (regimen.py D)


def carga():
    R = []
    for p in sorted(glob.glob(os.path.join(DIR, "stalepaper*.csv"))):
        with open(p, encoding="utf-8") as fh: R.extend(csv.DictReader(fh))
    return [r for r in R if r.get("trig") == TRIGID]


def num(r, k):
    try:
        v = float(r[k])
        return v if 0 < v < 1 else None
    except Exception:
        return None


def stat(v):
    if len(v) < 20: return None
    return 100 * st.mean(v), 100 * st.pstdev(v) / (len(v) ** 0.5)


def fmt(s):
    return f"{s[0]:+.2f} ± {s[1]:.2f}" if s else "—"


def actividad(filas):
    """Disparos en los 3600 s ANTERIORES. Trocea por huecos >1800 s (grabador parado, no calma)."""
    filas.sort(key=lambda f: f["t"])
    ini, j = filas[0]["t"], 0
    for i, f in enumerate(filas):
        f["act"] = None
        if i and f["t"] - filas[i - 1]["t"] > 1800: ini = f["t"]; continue
        while filas[j]["t"] < f["t"] - 3600: j += 1
        if f["t"] - ini >= 3600: f["act"] = i - j


def main():
    R = carga()
    print(f"disparos del disparador actual: {len(R)}")
    if len(R) < 500:
        print("muestra corta — dejar acumular"); return
    RES = _resol({int(r["ws"]) for r in R if r.get("ws")})

    filas = []
    for r in R:
        m = num(r, "mid8s")
        if m is None: continue
        try: t = float(r["ts_salto"])
        except Exception: continue
        asks = {k: num(r, f"ask{k}") for k in (0, 52, 100, 200)}
        if asks[0] is None: continue
        szs = {}
        for k in (0, 52, 100, 200):
            try: szs[k] = float(r[f"sz{k}"])
            except Exception: szs[k] = 0.0
        w = RES.get(int(r["ws"])) if r.get("ws") else None
        filas.append({"t": t, "mid": m, "ask": asks, "sz": szs,
                      "won": None if w is None else (1.0 if w == r.get("tok") else 0.0)})
    actividad(filas)
    print(f"utilizables: {len(filas)}")

    def analiza(v, titulo):
        print("\n" + "=" * 92)
        print(f"  {titulo}  (n = {len(v)})")
        print("=" * 92)
        print(f"  {'límite':>10}{'llega':>8}{'% relleno':>11}{'MEC si LLENA':>17}"
              f"{'MEC si NO llena':>18}{'RESOL si LLENA':>18}")
        for lim_k, extra in ((0, 0.0), (0, TICK), (52, 0.0)):
            for arr in (52, 100, 200):
                if arr <= lim_k: continue
                si_m, no_m, si_r, pagados, cortos = [], [], [], [], 0
                for f in v:
                    lim = f["ask"][lim_k]
                    a = f["ask"][arr]
                    if lim is None or a is None: continue
                    lim = round(lim + extra, 4)
                    if a <= lim + 1e-9:                      # se llena, al ask de llegada
                        p = a
                        si_m.append(f["mid"] - p - fee(p))
                        if f["won"] is not None: si_r.append(f["won"] - p - fee(p))
                        pagados.append(p)
                        if f["sz"][arr] < 5: cortos += 1
                    else:                                    # FAK: matado, no compramos nada
                        no_m.append(f["mid"] - f["ask"][lim_k] - fee(f["ask"][lim_k]))
                tot = len(si_m) + len(no_m)
                if tot < 50: continue
                et = f"ask{lim_k}" + (f"+{extra:.2f}"[1:] if extra else "")
                print(f"  {et:>10}{arr:>6}ms{100*len(si_m)/tot:>10.0f}%{fmt(stat(si_m)):>17}"
                      f"{fmt(stat(no_m)):>18}{fmt(stat(si_r)):>18}")
                if extra == 0.0 and arr == 100 and lim_k == 0 and pagados:
                    print(f"             └─ precio medio pagado {st.mean(pagados):.3f} · "
                          f"menos de 5 acciones en el ask: {100*cortos/max(len(si_m),1):.1f}% de los rellenos")

    analiza(filas, "TODOS LOS DISPAROS")
    tranq = [f for f in filas if f["act"] is not None and f["act"] <= MAX_ACT]
    if len(tranq) >= 200:
        analiza(tranq, f"SOLO RÉGIMEN TRANQUILO (≤{MAX_ACT} disparos/h causal) — donde opera el bot")

    print("\n" + "=" * 92)
    print("  CÓMO SE LEE")
    print("=" * 92)
    print("  · 'MEC si LLENA' es lo único que cobramos de verdad. Si es mucho menor que el margen que")
    print("    veníamos midiendo, la selección del relleno se está comiendo el edge.")
    print("  · 'MEC si NO llena' es el margen que se nos escapa. Si es MAYOR que el de los rellenos, nos")
    print("    están llenando justamente los peores casos y hay que replantear la ejecución.")
    print("  · El +1 tick solo compensa si el relleno sube lo bastante como para pagar ~1,8pp de precio.")
    print("  · OJO: esto supone que el ask que vemos es real y que nadie se nos adelanta. askrace.py dijo")
    print("    que el 67% de las cotizaciones rancias siguen vivas a 77 ms; esto es el techo, no el suelo.")


if __name__ == "__main__":
    try: sys.stdout.reconfigure(encoding="utf-8")
    except Exception: pass
    main()
