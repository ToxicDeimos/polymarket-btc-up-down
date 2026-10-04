"""
precio.py — ¿COMPENSA COMPRAR CARO? El margen por DÓLAR ARRIESGADO, no por nocional.

Lo planteó el usuario viendo el registro en vivo: el bot compró a 0,89 y a 0,70, y "no se puede
comprar tan caro porque las ganadoras no compensan". Tiene razón, y el fallo es de medida, mío:

  TODO lo que hemos medido en este proyecto está en PUNTOS DEL NOCIONAL de 1 $ (el markout, el
  +8,70 a resolución, el −2,57 de cruce). Esa unidad trata igual una ventaja de 2 céntimos a
  precio 0,89 que a precio 0,20. Pero el capital que hay que poner no es igual:

      a 0,89  →  5 acciones cuestan 4,45 $   →  2¢ de ventaja son un +2,2% del dinero puesto
      a 0,20  →  6 acciones cuestan 1,20 $   →  2¢ de ventaja son un +10% del dinero puesto

  Con una cuenta de 15 $ lo que limita es el CAPITAL, no el nocional. Y el riesgo tampoco es
  simétrico: a 0,89 una perdedora cuesta ocho veces lo que gana una ganadora, así que con pocas
  operaciones una sola mala borra ocho buenas.

Aquí se mide, sobre los disparos de PAPEL (que son miles, no 23), el resultado por franja de precio
en las dos unidades, y el tamaño real de la apuesta con la regla del bot:

      n = max(5, ceil(1,01/precio))        # mínimo del mercado y mínimo de 1 $ de nocional
      coste = n × precio                   # lo que sale de la cuenta
      neto  = n × (ganó − precio − fee)    # fee de taker = 0,07·p·(1−p)
      RETORNO SOBRE CAPITAL = neto / coste    ← la columna que de verdad manda

⚠ El relleno del papel es optimista (supone que si la cotización sigue ahí, es nuestra; en real
  llenamos el 6%, no el 61%). Vale para COMPARAR franjas entre sí, que es lo que se pregunta, no
  como estimación del beneficio.

    cd ~/polymarket-btc-up-down/research && python3 precio.py [pc|5m|15m]
"""
import csv, glob, math, os, sys, statistics as st

DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, DIR)
from stalepaper import fee, _resol, TRIGID      # noqa: E402

ALIAS = {"pc": "pcpaper", "15m": "paper15", "5m": "stalepaper"}
PREFIJO = ALIAS.get(sys.argv[1] if len(sys.argv) > 1 else "5m", "stalepaper")
MAX_ACT = 102                 # el filtro de régimen del bot
LLEGADA = 200                 # ms: la columna más pesimista que existe en el papel


def num(r, k):
    try:
        v = float(r[k])
        return v if 0 < v < 1 else None
    except Exception:
        return None


def carga():
    R = []
    for p in sorted(glob.glob(os.path.join(DIR, f"{PREFIJO}*.csv"))):
        if "_tarde" in os.path.basename(p): continue
        with open(p, encoding="utf-8", errors="replace") as fh: R.extend(csv.DictReader(fh))
    return [r for r in R if r.get("trig") == TRIGID]


def actividad(filas):
    filas.sort(key=lambda f: f["t"])
    ini, j = filas[0]["t"], 0
    for i, f in enumerate(filas):
        f["act"] = None
        if i and f["t"] - filas[i - 1]["t"] > 1800: ini = f["t"]; continue
        while filas[j]["t"] < f["t"] - 3600: j += 1
        if f["t"] - ini >= 3600: f["act"] = i - j


def main():
    R = carga()
    print(f"{PREFIJO}: {len(R)} disparos del disparador actual")
    if not R: return
    RES = _resol({int(r["ws"]) for r in R if r.get("ws")})
    print(f"ventanas resueltas: {len(RES)}")

    filas = []
    for r in R:
        try: ws, t = int(r["ws"]), float(r["ts_salto"])
        except Exception: continue
        g = RES.get(ws)
        p = num(r, "ask0")
        pl = num(r, f"ask{LLEGADA}")
        if g is None or p is None: continue
        filas.append({"t": t, "p": p, "pl": pl, "tok": r.get("tok"), "g": g})
    if not filas:
        print("sin disparos resueltos"); return
    actividad(filas)

    CORTES = [0.15, 0.30, 0.45, 0.60, 0.75, 0.88]
    ETI = ["<0,15", "0,15-0,30", "0,30-0,45", "0,45-0,60", "0,60-0,75", "0,75-0,88", ">0,88"]

    def cubo(p):
        for i, c in enumerate(CORTES):
            if p < c: return i
        return len(CORTES)

    def informe(sel, titulo):
        print("\n" + "=" * 100)
        print(f"  {titulo}  (n = {len(sel)})")
        print("=" * 100)
        print(f"    {'precio':<13}{'n':>7}{'llena':>8}{'coste/op':>10}{'pp nocional':>14}"
              f"{'RETORNO CAPITAL':>18}{'$ puesto':>11}{'$ neto':>10}")
        tot = [0, 0.0, 0.0]
        for i in range(len(CORTES) + 1):
            g = [f for f in sel if cubo(f["p"]) == i]
            if len(g) < 20: continue
            # solo las que se habrían llenado: la cotización sigue ahí al llegar
            ll = [f for f in g if f["pl"] is not None and f["pl"] <= f["p"]]
            if len(ll) < 20: continue
            cap = net = 0.0; pps = []
            for f in ll:
                p = f["p"]
                n = max(5, math.ceil(1.01 / p))
                gano = 1.0 if f["tok"] == f["g"] else 0.0
                por_accion = gano - p - fee(p)
                cap += n * p; net += n * por_accion; pps.append(por_accion)
            tot[0] += len(ll); tot[1] += cap; tot[2] += net
            m = 100 * st.mean(pps)
            e = 100 * st.pstdev(pps) / len(pps) ** 0.5
            print(f"    {ETI[i]:<13}{len(g):>7}{100*len(ll)/len(g):>7.0f}%"
                  f"{cap/len(ll):>10.2f}{f'{m:+.2f}±{e:.2f}':>14}"
                  f"{f'{100*net/cap:+.2f}%':>18}{cap:>11,.0f}{net:>10,.0f}")
        if tot[1]:
            print(f"    {'TOTAL':<13}{'':>7}{'':>8}{'':>10}{'':>14}"
                  f"{f'{100*tot[2]/tot[1]:+.2f}%':>18}{tot[1]:>11,.0f}{tot[2]:>10,.0f}")

    informe(filas, "TODOS LOS DISPAROS")
    q = [f for f in filas if f["act"] is not None and f["act"] <= MAX_ACT]
    if len(q) >= 100:
        informe(q, f"SOLO RÉGIMEN TRANQUILO (≤{MAX_ACT} disparos/h) — donde opera el bot")

    print("\n" + "=" * 100)
    print("  'pp nocional' es como hemos medido SIEMPRE: puntos sobre 1 $ de nocional.")
    print("  'RETORNO CAPITAL' es lo que de verdad le pasa a la cuenta: neto / dinero puesto.")
    print("  Si las dos columnas ordenan las franjas de forma DISTINTA, hemos estado optimizando")
    print("  la unidad equivocada — y entonces el tope de precio del bot (hoy 0,95) está mal puesto.")
    print("=" * 100)


if __name__ == "__main__":
    try: sys.stdout.reconfigure(encoding="utf-8")
    except Exception: pass
    main()
