"""
EXPERIMENTO #5b — LA PREGUNTA DECISIVA: ¿el mercado PRECIA la volatilidad?

hist_backtest.py (1 año, 104.698 ventanas) encontró que el hit rate de nuestra señal depende
FUERTE y monótonamente de la VOLATILIDAD (vol baja 82.8% → vol alta 67.2%, 15,5pp), con mecanismo
físico claro: la señal es una ventaja de tamaño FIJO (1-4 bps) contra 60s de ruido →
P(aguanta) ≈ Φ(move / ruido_restante). Tendencia/chop (autocorrelación) salió PLANA = muerta.

Pero eso NO basta para tener edge: si el mercado ya cobra menos cuando hay más ruido, la vol está
priceada y no hay nada que rascar. Este script responde justo eso, cruzando el ASK REAL que logueó
nuestro bot (momentum_paper_log.csv, ~1400 ventanas) con la volatilidad de cada ventana:

  A) ¿varía el ask con la vol, CONTROLANDO POR MOVE?  (el ask depende sobre todo del move: hay que
     comparar vol alta vs baja DENTRO de cada tramo de move, o el efecto del move lo tapa todo)
       · si el ask BAJA cuando sube la vol → el mercado SÍ ajusta → vol priceada, no hay edge ahí
       · si el ask se queda PLANO           → NO ajusta → mispricing → filtro "operar solo vol baja"
  B) hit rate HISTÓRICO (año de velas) en esas MISMAS celdas move×vol
  C) EDGE = hit histórico − ask observado, celda a celda = el mapa de dónde comprar/evitar

SIN LOOKAHEAD: la vol de cada ventana usa SOLO las 288 ventanas (24h) ANTERIORES.

    python ask_vs_vol.py
Necesita: lab/klines_1m_btc.csv (lo genera hist_backtest.py) y momentum_paper_log.csv.
Autónomo (stdlib).
"""
import os, sys, csv, math, time
from collections import deque

DIR   = os.path.join(os.path.dirname(__file__), "lab")
CACHE = os.path.join(DIR, "klines_1m_btc.csv")
LOG   = os.path.join(os.path.dirname(__file__), "momentum_paper_log.csv")
REF_PRICE = 118000.0
TRAIL = 288                      # ventanas 5m de historia para la vol (24 h)
MOVE_BUCKETS = [(0.68, 1.50, "0.68-1.5 bps ($8-18)"),
                (1.50, 2.50, "1.5-2.5 bps ($18-30)"),
                (2.50, 3.81, "2.5-3.81 bps ($30-45)")]

def load_cache():
    if not os.path.exists(CACHE):
        print(f"falta {CACHE} — corre antes:  python3 hist_backtest.py 365"); sys.exit(1)
    d = {}
    with open(CACHE, encoding="utf-8") as f:
        for ln in f:
            a = ln.split(",")
            try: d[int(a[0])] = float(a[1])
            except Exception: pass
    return d

def build(cache):
    """Ventanas 5m con move/resultado + VOL trailing 24h sin lookahead (varianza rodante O(n))."""
    ks = sorted(cache)
    if not ks: return [], {}
    W = []
    for ws in range((ks[0] // 300 + 1) * 300, ks[-1] - 300, 300):
        o = cache.get(ws); e = cache.get(ws + 240); c = cache.get(ws + 300)
        if o is None or e is None or c is None or not o: continue
        bps = (e - o) / o * 10000
        if bps == 0: continue
        W.append({"ws": ws, "abps": abs(bps), "ret": (c - o) / o,
                  "won": 1 if (("Up" if c >= o else "Down") == ("Up" if bps > 0 else "Down")) else 0})
    vols, dq, s, s2 = {}, deque(), 0.0, 0.0
    for w in W:
        if len(dq) == TRAIL:
            m = s / TRAIL
            vols[w["ws"]] = math.sqrt(max(s2 / TRAIL - m * m, 0.0)) * 10000
        dq.append(w["ret"]); s += w["ret"]; s2 += w["ret"] ** 2
        if len(dq) > TRAIL:
            old = dq.popleft(); s -= old; s2 -= old ** 2
    for w in W: w["vol"] = vols.get(w["ws"])
    return W, vols

def load_log(vols):
    """Ventanas que NUESTRO bot evaluó, con el ask REAL cobrado y su vol."""
    if not os.path.exists(LOG):
        print(f"falta {LOG} (el log del bot)"); sys.exit(1)
    R = []
    for r in csv.DictReader(open(LOG, encoding="utf-8")):
        try:
            ws = int(r["ws"]); mv = abs(float(r["move"])); ak = float(r["ask"])
        except Exception: continue
        if not (0 < ak < 1): continue
        v = vols.get(ws)
        if v is None: continue
        R.append({"ws": ws, "abps": mv / REF_PRICE * 10000, "ask": ak, "vol": v,
                  "won": (int(r["won"]) if r.get("won") in ("0", "1") else None)})
    return R

def mean(xs): return sum(xs) / len(xs) if xs else None

def ci(p, n):
    se = math.sqrt(p * (1 - p) / n)
    return max(0, p - 1.96 * se), min(1, p + 1.96 * se)

def main():
    try: sys.stdout.reconfigure(encoding="utf-8")
    except Exception: pass
    cache = load_cache()
    print(f"velas 1m en caché: {len(cache)}")
    W, vols = build(cache)
    print(f"ventanas históricas con vol: {sum(1 for w in W if w['vol'] is not None)}")
    R = load_log(vols)
    if len(R) < 100:
        print(f"solo {len(R)} ventanas del log con vol — ¿el log es más antiguo que la caché?"); return
    lv = sorted(x["vol"] for x in R)
    q1, q2 = lv[len(lv) // 3], lv[2 * len(lv) // 3]
    print(f"ventanas del bot con ask real y vol: {len(R)}")
    print(f"rango de vol en NUESTRAS ventanas: {lv[0]:.1f} – {lv[-1]:.1f} bps  "
          f"(terciles en {q1:.1f} y {q2:.1f})")
    print(f"  [contexto: en el año, los quintiles de vol caen ~7.4 / 9.5 / 11.6 / 14.6 bps]")
    TER = [(-1e9, q1, "vol BAJA"), (q1, q2, "vol MEDIA"), (q2, 1e9, "vol ALTA")]

    print("\n" + "=" * 92)
    print("A) ¿EL MERCADO AJUSTA EL ASK A LA VOLATILIDAD?  (controlando por MOVE)")
    print("=" * 92)
    print("   si el ask BAJA al subir la vol → el mercado SÍ precia el ruido → no hay edge ahí")
    print("   si se queda PLANO → NO lo precia → mispricing explotable\n")
    print(f"   {'tramo de move':<24} {'vol BAJA':>18} {'vol MEDIA':>18} {'vol ALTA':>18}   {'Δ(alta−baja)':>13}")
    for lo, hi, lab in MOVE_BUCKETS:
        seg = [x for x in R if lo <= x["abps"] < hi]
        cells, asks = [], []
        for vlo, vhi, _ in TER:
            c = [x for x in seg if vlo <= x["vol"] < vhi]
            a = mean([x["ask"] for x in c])
            cells.append(f"{a:.1%} (n={len(c)})" if a else "—")
            asks.append(a)
        d = (asks[2] - asks[0]) * 100 if (asks[0] and asks[2]) else None
        print(f"   {lab:<24} {cells[0]:>18} {cells[1]:>18} {cells[2]:>18}   "
              f"{(f'{d:+.2f}pp' if d is not None else '—'):>13}")

    print("\n" + "=" * 92)
    print("B) HIT RATE HISTÓRICO (1 año de velas) en las MISMAS celdas move × vol")
    print("=" * 92)
    print(f"   {'tramo de move':<24} {'vol BAJA':>18} {'vol MEDIA':>18} {'vol ALTA':>18}   {'Δ(alta−baja)':>13}")
    HIT = {}
    for lo, hi, lab in MOVE_BUCKETS:
        seg = [w for w in W if w["vol"] is not None and lo <= w["abps"] < hi]
        cells, hits = [], []
        for vlo, vhi, vlab in TER:
            c = [w for w in seg if vlo <= w["vol"] < vhi]
            h = mean([w["won"] for w in c]) if c else None
            HIT[(lab, vlab)] = (h, len(c))
            cells.append(f"{h:.1%} (n={len(c)})" if h is not None else "—")
            hits.append(h)
        d = (hits[2] - hits[0]) * 100 if (hits[0] is not None and hits[2] is not None) else None
        print(f"   {lab:<24} {cells[0]:>18} {cells[1]:>18} {cells[2]:>18}   "
              f"{(f'{d:+.2f}pp' if d is not None else '—'):>13}")

    print("\n" + "=" * 92)
    print("C) EDGE por celda = hit histórico − ask observado   (+ = líder INFRAvalorado → comprar)")
    print("=" * 92)
    print(f"   {'tramo de move':<24} {'vol BAJA':>18} {'vol MEDIA':>18} {'vol ALTA':>18}")
    for lo, hi, lab in MOVE_BUCKETS:
        seg = [x for x in R if lo <= x["abps"] < hi]
        cells = []
        for vlo, vhi, vlab in TER:
            c = [x for x in seg if vlo <= x["vol"] < vhi]
            a = mean([x["ask"] for x in c]); h, hn = HIT.get((lab, vlab), (None, 0))
            if a is None or h is None: cells.append("—"); continue
            e = (h - a) * 100
            cells.append(f"{e:+.1f}pp{'  ***' if abs(e) > 5 else ''}")
        print(f"   {lab:<24} {cells[0]:>18} {cells[1]:>18} {cells[2]:>18}")

    print("\n" + "=" * 92)
    print("CÓMO LEER")
    print("=" * 92)
    print("A vs B es TODO el experimento:")
    print("  · B (realidad) YA sabemos que cae fuerte con la vol (−15pp en el año).")
    print("  · Si en A el ask cae PARECIDO → el mercado precia el ruido: mercado eficiente, sin edge.")
    print("  · Si en A el ask apenas se mueve → cobra lo mismo por algo que pasa mucho menos:")
    print("    ese hueco es el edge, y el filtro sería 'operar solo en vol BAJA'.")
    print("\nCAVEATS honestos:")
    print("  · nuestras ventanas son de UNA semana: puede que no cubran todo el espectro de vol del")
    print("    año (mira el rango impreso arriba). Si es estrecho, el test es indicativo, no final.")
    print("  · el ask logueado es el del LÍDER en el momento de evaluar (240s), que es justo el")
    print("    precio que pagaríamos. Eso sí es limpio.")
    print("  · C mezcla ask de nuestra semana con hit de todo el año: si nuestra semana fue atípica,")
    print("    el nivel absoluto se desplaza. El patrón ENTRE columnas es lo fiable.")

if __name__ == "__main__":
    main()
