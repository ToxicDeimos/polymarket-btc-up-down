"""
EXPERIMENTO #11b — predice el destino de MAKER3 con POTENCIA, sin esperar a sus fills.

maker3 en vivo lleva n=5 (ruido). En vez de esperar días a los 40 fills, simulamos su estrategia EXACTA
sobre los MILES de ventanas de la cinta histórica (tape_*.csv): bid descansando a TARGET en ambos lados,
fill al PRIMER roce (primer trade a <=TARGET en cualquiera de los dos lados), aguantar a resolución.

  · fill_side = el lado del primer trade ejecutado a <= TARGET (el despreciado que cayó a nuestro bid).
  · won = ese lado ganó (resolución por CHAINLINK donde hay dato, spot si no; NUNCA para veredicto por-
    trade fino, pero aquí es agregado masivo = válido).
  · EDGE = win − TARGET.

Si el win SUPERA el TARGET (35%) sobre miles de ventanas → maker3 es +EV en la población → el rojo de
n=5 en vivo es ruido y convergerá. Si el win <= TARGET → el despreciado que toca 35¢ NO revierte lo
suficiente → maker3 es −EV de verdad, y el early-rester tampoco captura el edge. Respuesta con potencia.

    python maker3_backtest.py
Autónomo (stdlib).
"""
import os, sys, csv, glob, math, bisect

DIR    = os.path.join(os.path.dirname(__file__), "lab")
TARGET = 0.35

def load(name, days):
    rows = []
    for d in days:
        p = os.path.join(DIR, f"{name}_{d}.csv")
        if os.path.exists(p): rows += list(csv.DictReader(open(p, encoding="utf-8")))
    return rows

def series(rows):
    idx = sorted((int(r["ts"]), float(r["price"])) for r in rows if r.get("price"))
    ks = [x[0] for x in idx]
    def at(ts, maxage):
        i = bisect.bisect_right(ks, ts) - 1
        return idx[i][1] if i >= 0 and ts - idx[i][0] <= maxage else None
    return at

def line(label, rs, T):
    if not rs:
        print(f"  {label:<24} (sin ventanas)"); return
    n = len(rs); wr = sum(r["won"] for r in rs) / n
    se = math.sqrt(wr * (1 - wr) / n); edge = (wr - T) * 100
    lo, hi = max(0, wr - 1.96 * se), min(1, wr + 1.96 * se)
    rel = edge / (T * 100) * 100
    sig = "SIG" if lo > T else ("+" if wr > T else "")
    print(f"  {label:<24} n={n:>6}  win {wr:6.2%}  target {T:.0%}  EDGE {edge:+6.2f}pp  "
          f"rel {rel:+6.1f}%  [IC {lo:.1%}-{hi:.1%}] {sig}")

def main():
    try: sys.stdout.reconfigure(encoding="utf-8")
    except Exception: pass
    days = sorted({os.path.basename(p).split("_")[1][:8] for p in glob.glob(os.path.join(DIR, "tape_*.csv"))})
    if not days: print("sin tape_*.csv"); return
    tape = load("tape", days)
    lcl, lsp = series(load("chainlink", days)), series(load("spot", days))
    print(f"días: {', '.join(days)}  |  cinta {len(tape)}  |  simulando maker3 (primer roce, barrido de targets)")

    # agrupa trades por ventana (ws del slug); solo BUY? no: cualquier trade a <=TARGET indica que el
    # precio de ese lado tocó nuestro bid → nos habría llenado (somos el primero por llegar temprano).
    W = {}
    for x in tape:
        slug = x.get("slug", "") or ""
        if "-5m-" not in slug: continue
        try:
            ws = int(slug.split("-")[-1]); tp = float(x["price"]); tt = int(x["ts_trade"])
        except Exception: continue
        if not (0 < tp < 1): continue
        W.setdefault(ws, {"cid": x.get("cid"), "trades": []})["trades"].append((tt, tp, x.get("outcome")))

    # precomputa por ventana: trades ordenados por tiempo + ganador (resolución independiente del target)
    WR = {}
    for ws, w in W.items():
        o, c = lcl(ws, 60), lcl(ws + 300, 60)
        if o is None or c is None: o, c = lsp(ws, 12), lsp(ws + 300, 12)
        if o is None or c is None: continue
        winner = "Up" if c >= o else "Down"
        trades = sorted((tt, tp, side) for tt, tp, side in w["trades"] if side in ("Up", "Down"))
        WR[ws] = (trades, winner)
    if len(WR) < 100:
        print(f"solo {len(WR)} ventanas resueltas — deja acumular la cinta"); return

    def sim(T):
        R = []
        for ws, (trades, winner) in WR.items():
            cand = next(((tt, tp, side) for tt, tp, side in trades if tp <= T), None)  # primer roce a <=T
            if cand: R.append({"won": 1 if cand[2] == winner else 0, "phase": cand[0] - ws})
        return R

    print(f"\nventanas resueltas: {len(WR)}\n")
    print("=" * 92)
    print("BARRIDO DE TARGET — ¿hay ALGÚN precio al que el despreciado que toca T REVIERTE (gana > T)?")
    print("=" * 92)
    best = None
    for T in (0.08, 0.12, 0.15, 0.20, 0.25, 0.30, 0.35, 0.40):
        R = sim(T)
        if len(R) < 50: continue
        line(f"bid a {T:.0%}", R, T)
        wr = sum(r["won"] for r in R) / len(R); se = math.sqrt(wr * (1 - wr) / len(R))
        if wr - 1.96 * se > T and (best is None or (wr - T) > best[1]): best = (T, wr - T)

    print("\n" + "=" * 92)
    print("VEREDICTO (con potencia, sobre miles de ventanas)")
    print("=" * 92)
    if best:
        print(f"  → SÍ hay edge SIGNIFICATIVO: mejor target {best[0]:.0%} (EDGE +{best[1]*100:.1f}pp). El early-")
        print(f"    rester funciona PROFUNDO, no a 35¢. Cambiar el TARGET de maker3 a {best[0]:.0%} y re-medir en vivo.")
        print("\n  — por FASE al mejor target:")
        R = sim(best[0])
        for lo, hi, lab in [(0, 60, "0-60s"), (60, 120, "60-120s"), (120, 195, "120-195s"),
                            (195, 300, "195-300s"), (300, 1e9, ">300")]:
            line(lab, [r for r in R if lo <= r["phase"] < hi], best[0])
    else:
        print("  → NINGÚN target da +EV: el 'primer roce' (comprar el lado que cae a nuestro bid) es")
        print("    adverso a TODO precio. El despreciado que toca cualquier nivel sigue cayendo, no revierte.")
        print("    maker3 (early-rester comprando el que cae) está MUERTO a todos los precios. Cierre honesto.")

    print("\nCAVEATS: cinta muestreada cada 20s → 'primer roce' aproximado. Precio de fill = target. Resolución")
    print("agregada por Chainlink/spot. El barrido responde si existe UN precio con edge, no lo optimiza fino.")

if __name__ == "__main__":
    main()
