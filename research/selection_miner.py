"""
EXPERIMENTO #15 — MINERO DE SELECCIÓN: ¿qué feature usan los ganadores para elegir QUÉ favorito?

winners_deep probó: los ganadores son reales (z+32..+4), su edge está en los FAVORITOS (62-95¢), y ganan
+5-12pp porque SELECCIONAN (ganan ~82% donde el favorito medio gana ~68-70% en 62-72¢, ~90% en 82-95¢).
La pregunta que lo decide todo: ¿esa selección está en una feature OBSERVABLE (replicable) o es skill oculto?

Usa los fills COMPLETOS de los ganadores en la zona favorito como ETIQUETAS (miles, un año) y computa, para
cada uno, features de las velas 1m ya cacheadas (klines_1m_btc.csv):
  · move_bps a 240s (fuerza del favorito)   · vol trailing 24h (régimen)   · aceleración (últimos 60s vivos)
Parte el win rate de los ganadores por cada feature. Si una feature SUBE su win claramente (y el favorito
del montón también sube ahí) → es la regla de selección, replicable. Si es plano → skill no observable.
Compara con la línea base: el LÍDER del montón (todas las ventanas de klines) por la misma feature.

    python selection_miner.py
Necesita lab/klines_1m_btc.csv y los caches lab/wtrades_*.csv (los genera winners_deep.py). Autónomo.
"""
import os, sys, csv, glob, math

DIR = os.path.join(os.path.dirname(__file__), "lab")
KL  = os.path.join(DIR, "klines_1m_btc.csv")
TRAIL = 288   # ventanas 5m para la vol (24h)

def load_klines():
    if not os.path.exists(KL):
        print(f"falta {KL} — corre: python3 hist_backtest.py 365"); sys.exit(1)
    d = {}
    with open(KL, encoding="utf-8") as f:
        for ln in f:
            a = ln.split(",")
            try: d[int(a[0])] = float(a[1])
            except Exception: pass
    return d

def wr_line(label, seg):
    if not seg:
        print(f"     {label:<20} (sin datos)"); return
    n = len(seg); w = sum(seg) / n
    se = math.sqrt(w * (1 - w) / n)
    print(f"     {label:<20} n={n:>6}  win {w:6.2%}  (IC {max(0,w-1.96*se):.1%}-{min(1,w+1.96*se):.1%})")

def main():
    try: sys.stdout.reconfigure(encoding="utf-8")
    except Exception: pass
    kl = load_klines()
    kws = sorted(k for k in kl if k % 300 == 0)          # inicios de ventana 5m
    print(f"velas 1m: {len(kl)}  ·  ventanas 5m alineadas: {len(kws)}")

    # vol trailing por ventana (std de retornos 5m de las TRAIL anteriores)
    rets = {}
    for ws in kws:
        o, c = kl.get(ws), kl.get(ws + 300)
        if o and c: rets[ws] = (c - o) / o
    order = [ws for ws in kws if ws in rets]
    vol = {}
    for i, ws in enumerate(order):
        if i < TRAIL: continue
        prev = [rets[order[j]] for j in range(i - TRAIL, i)]
        m = sum(prev) / len(prev)
        vol[ws] = math.sqrt(sum((x - m) ** 2 for x in prev) / len(prev)) * 10000  # bps

    def feats(ws):
        o, e, e60 = kl.get(ws), kl.get(ws + 240), kl.get(ws + 180)
        if o is None or e is None or e60 is None: return None
        move = (e - o) / o * 10000
        if move == 0: return None
        accel = ((e - e60) > 0) == (move > 0)            # el move sigue vivo en los últimos 60s
        return {"move": abs(move), "sgn": "Up" if move > 0 else "Down", "accel": accel, "vol": vol.get(ws)}

    # fills COMPLETOS de los ganadores en la zona favorito (62-95¢)
    W = []
    for p in glob.glob(os.path.join(DIR, "wtrades_*.csv")):
        for r in csv.DictReader(open(p, encoding="utf-8")):
            try:
                ws = int(r["slug"].split("-")[-1]); pr = float(r["price"])
            except Exception: continue
            if r.get("side") != "BUY" or not (0.62 <= pr <= 0.95): continue
            o, c = kl.get(ws), kl.get(ws + 300)
            if o is None or c is None: continue
            won = 1 if r["outcome"] == ("Up" if c >= o else "Down") else 0
            f = feats(ws)
            if f is None or f["vol"] is None: continue
            W.append({"won": won, **f})
    if len(W) < 300:
        print(f"solo {len(W)} fills favorito de ganadores con features — ¿faltan wtrades_*.csv? corre winners_deep.py"); return

    base = sum(x["won"] for x in W) / len(W)
    print(f"\nfills FAVORITO (62-95¢) de los ganadores con features: {len(W)}  ·  win base {base:.1%}\n")

    print("=" * 84)
    print("¿QUÉ FEATURE separa los favoritos GANADORES de los ganadores? (base = su win medio)")
    print("=" * 84)
    print("  por VOLATILIDAD trailing 24h (¿eligen régimen tranquilo?):")
    vs = sorted(x["vol"] for x in W); q = [vs[int(len(vs)*f)] for f in (.25, .5, .75)]
    for lo, hi, lab in [(-1, q[0], "Q1 baja"), (q[0], q[1], "Q2"), (q[1], q[2], "Q3"), (q[2], 1e9, "Q4 alta")]:
        wr_line(lab, [x["won"] for x in W if lo <= x["vol"] < hi])
    print("  por FUERZA del move a 240s (bps):")
    for lo, hi, lab in [(0, 3, "<3 bps"), (3, 6, "3-6"), (6, 12, "6-12"), (12, 1e9, ">12 bps")]:
        wr_line(lab, [x["won"] for x in W if lo <= x["move"] < hi])
    print("  por ACELERACIÓN (move vivo en los últimos 60s):")
    wr_line("acelera", [x["won"] for x in W if x["accel"]])
    wr_line("frena",   [x["won"] for x in W if not x["accel"]])

    # LÍNEA BASE: el LÍDER del montón (todas las ventanas) por las mismas features
    print("\n" + "=" * 84)
    print("LÍNEA BASE — el LÍDER del MONTÓN (todas las ventanas de klines) por feature")
    print("=" * 84)
    P = []
    for ws in order:
        f = feats(ws)
        if f is None or f["vol"] is None or f["move"] < 3: continue   # favorito claro (move fuerte)
        o, c = kl.get(ws), kl.get(ws + 300)
        won = 1 if f["sgn"] == ("Up" if c >= o else "Down") else 0
        P.append({"won": won, **f})
    pbase = sum(x["won"] for x in P) / len(P) if P else 0
    print(f"  líder del montón (move≥3bps): n={len(P)}  win base {pbase:.1%}")
    print("  por VOLATILIDAD:")
    for lo, hi, lab in [(-1, q[0], "Q1 baja"), (q[0], q[1], "Q2"), (q[1], q[2], "Q3"), (q[2], 1e9, "Q4 alta")]:
        wr_line(lab, [x["won"] for x in P if lo <= x["vol"] < hi])

    print("\n" + "=" * 84)
    print("CÓMO LEER")
    print("=" * 84)
    print("· Si el win de los ganadores SUBE claramente en una feature Y el líder del montón sube igual")
    print("  ahí → esa feature es la selección, y es REPLICABLE (comprar el favorito solo cuando feature X).")
    print("· Si el win de los ganadores es PLANO en todas → su selección NO está en move/vol/accel:")
    print("  es skill oculto o info que no tenemos. Habría que probar features de LIBRO (solo 10 días).")
    print("· Compara ganadores vs montón: si los ganadores ganan MÁS que el líder del montón al MISMO")
    print("  nivel de feature → hay selección más allá de estas features (skill).")

if __name__ == "__main__":
    main()
