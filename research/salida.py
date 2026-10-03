"""
salida.py — ¿COMPENSA VENDER ANTES DE QUE RESUELVA?

La pregunta natural al tener una posición abierta: ¿y si pongo un stop, o salgo cuando ya he ganado?
`stalebt` lo midió hace dos semanas y salía que no (aguantar +2,12 contra vender a 8 s +0,36), pero
eso era con otros datos y sin los plazos largos. Esto lo recalcula con lo de ahora.

CÓMO SE CUENTA, que es donde está todo:
  · aguantar   →  resultado − ask − comisión(ask)          UNA comisión: resolver no cobra
  · vender a T →  bid_T − comisión(bid_T) − ask − comisión(ask)   DOS comisiones, y cruzas el spread
  La comisión es 0,07·p·(1−p) por acción, ~1,73pp a precio 0,55. Pagarla dos veces son ~3,5pp de un
  margen de ~7. Por eso la salida es cara aunque aciertes.

⚠ El bid al que venderíamos NO está registrado: solo tenemos el MEDIO a cada plazo y el spread de
  entrada. Se estima bid_T ≈ mid_T − spread/2, y se muestra la sensibilidad a que el spread sea la
  mitad o el doble, porque esa suposición es la parte más débil del cálculo.

⚠ Esto compara ESTRATEGIAS DE SALIDA sobre los mismos disparos, no predice el relleno. La selección
  de lo que se llena afecta igual a todas las ramas, así que la comparación relativa se sostiene.

    cd ~/polymarket-btc-up-down/research && python3 salida.py [prefijo]
"""
import csv, glob, os, sys, statistics as st

DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, DIR)
from stalepaper import fee, _resol, TRIGID, TARDE      # noqa: E402

ALIAS = {"pc": "pcpaper", "15m": "paper15", "5m": "stalepaper"}
PREFIJO = ALIAS.get(sys.argv[1], sys.argv[1]) if len(sys.argv) > 1 else "stalepaper"
MAX_ACT = 104


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
    tarde = {}
    for p in sorted(glob.glob(os.path.join(DIR, f"{PREFIJO}_tarde*.csv"))):
        for r in csv.DictReader(open(p, encoding="utf-8")):
            if r.get("trig") == TRIGID: tarde[r["ts_salto"]] = r
    R = []
    for p in sorted(glob.glob(os.path.join(DIR, f"{PREFIJO}*.csv"))):
        if "_tarde" in os.path.basename(p): continue
        R += [r for r in csv.DictReader(open(p, encoding="utf-8")) if r.get("trig") == TRIGID]
    print(f"serie {PREFIJO} · {len(R)} disparos · {len(tarde)} con plazos largos")
    if not R or not tarde: print("sin datos suficientes"); return

    RES = _resol({int(r["ws"]) for r in R if r.get("ws")})
    cols = [f"mid{int(h)}s" for h in TARDE]
    filas = []
    for r in R:
        a, sp = num(r.get("ask52")), r.get("spread0")
        tr = tarde.get(r["ts_salto"])
        if a is None or not tr: continue
        try: t, sp = float(r["ts_salto"]), float(sp)
        except Exception: continue
        w = RES.get(int(r["ws"])) if r.get("ws") else None
        if w is None: continue
        mids = [num(tr.get(c)) for c in cols]
        if any(m is None for m in mids): continue
        filas.append({"t": t, "a": a, "sp": sp, "mids": mids,
                      "won": 1.0 if w == r.get("tok") else 0.0})
    if len(filas) < 50:
        print(f"solo {len(filas)} disparos con plazos Y resolución — dejar acumular"); return
    actividad(filas)

    def tabla(v, titulo):
        print("\n" + "=" * 74)
        print(f"  {titulo}   (n = {len(v)})")
        print("=" * 74)
        base = stat([x["won"] - x["a"] - fee(x["a"]) for x in v])
        print(f"  {'estrategia':>22}{'neto por operación':>22}{'vs aguantar':>16}")
        print(f"  {'AGUANTAR a resolución':>22}{fmt(base):>22}{'—':>16}")
        for k, h in enumerate(TARDE):
            for etiq, mult in (("", 1.0),):
                n = []
                for x in v:
                    bid = x["mids"][k] - mult * x["sp"] / 2
                    if not (0 < bid < 1): continue
                    n.append(bid - fee(bid) - x["a"] - fee(x["a"]))
                s = stat(n)
                dif = (s[0] - base[0]) if (s and base) else None
                print(f"  {f'vender a {int(h)} s':>22}{fmt(s):>22}"
                      f"{(f'{dif:+.2f}' if dif is not None else '—'):>16}")
        # sensibilidad: la estimacion del bid es la parte floja
        print(f"\n  sensibilidad a la suposición del bid (vender a {int(TARDE[-1])} s):")
        for etiq, mult in (("spread la mitad", 0.5), ("spread tal cual", 1.0), ("spread el doble", 2.0)):
            n = []
            for x in v:
                bid = x["mids"][-1] - mult * x["sp"] / 2
                if 0 < bid < 1: n.append(bid - fee(bid) - x["a"] - fee(x["a"]))
            print(f"    {etiq:>18}  {fmt(stat(n))}")

    tabla(filas, "TODOS LOS DISPAROS")
    tr = [f for f in filas if f["act"] is not None and f["act"] <= MAX_ACT]
    if len(tr) >= 50: tabla(tr, f"SOLO RÉGIMEN TRANQUILO (≤{MAX_ACT}/h causal)")

    print("\n" + "=" * 74)
    print("  CÓMO SE LEE")
    print("=" * 74)
    print("  · La columna 'vs aguantar' es lo que CUESTA salir antes. Si es negativa en todos los")
    print("    plazos, aguantar gana y no hay stop ni salida que lo mejore.")
    print("  · Un stop-loss es aún peor que estas cifras: aquí el precio ES la probabilidad, así que")
    print("    vender tras un movimiento en contra realiza la pérdida a valor justo y encima paga la")
    print("    segunda comisión. Y la pérdida máxima ya está acotada a lo que pusiste.")
    print("  · Si alguna fila saliera POSITIVA, antes de creérsela: comprobar la sensibilidad del")
    print("    spread, porque el bid es estimado y es la suposición más débil de todo esto.")


if __name__ == "__main__":
    try: sys.stdout.reconfigure(encoding="utf-8")
    except Exception: pass
    main()
