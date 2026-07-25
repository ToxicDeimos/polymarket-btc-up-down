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

def line(label, rs):
    if not rs:
        print(f"  {label:<24} (sin ventanas)"); return
    n = len(rs); wr = sum(r["won"] for r in rs) / n
    se = math.sqrt(wr * (1 - wr) / n); edge = (wr - TARGET) * 100
    lo, hi = max(0, wr - 1.96 * se), min(1, wr + 1.96 * se)
    rel = edge / (TARGET * 100) * 100
    sig = "SIG" if lo > TARGET else ("+" if wr > TARGET else "")
    print(f"  {label:<24} n={n:>6}  win {wr:6.2%}  target {TARGET:.0%}  EDGE {edge:+6.2f}pp  "
          f"rel {rel:+6.1f}%  [IC {lo:.1%}-{hi:.1%}] {sig}")

def main():
    try: sys.stdout.reconfigure(encoding="utf-8")
    except Exception: pass
    days = sorted({os.path.basename(p).split("_")[1][:8] for p in glob.glob(os.path.join(DIR, "tape_*.csv"))})
    if not days: print("sin tape_*.csv"); return
    tape = load("tape", days)
    lcl, lsp = series(load("chainlink", days)), series(load("spot", days))
    print(f"días: {', '.join(days)}  |  cinta {len(tape)}  |  simulando maker3 (bid {TARGET} ambos lados, primer roce)")

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

    R = []
    for ws, w in W.items():
        # primer trade (por tiempo) a <= TARGET → ahí nos habría llenado (primer roce)
        cand = sorted((tt, tp, side) for tt, tp, side in w["trades"] if tp <= TARGET and side in ("Up", "Down"))
        if not cand: continue                            # ningún lado tocó 35¢ = no_fill
        tt, tp, side = cand[0]
        o, c = lcl(ws, 60), lcl(ws + 300, 60)
        if o is None or c is None: o, c = lsp(ws, 12), lsp(ws + 300, 12)
        if o is None or c is None: continue
        winner = "Up" if c >= o else "Down"
        R.append({"won": 1 if side == winner else 0, "phase": tt - ws})
    if len(R) < 100:
        print(f"solo {len(R)} ventanas con roce a {TARGET} — deja acumular la cinta"); return

    ncl = "chainlink" if lcl(list(W)[0] if W else 0, 60) is not None else "spot"
    print(f"\nventanas donde un lado tocó {TARGET} (nos habríamos llenado): {len(R)}\n")
    print("=" * 92)
    print(f"MAKER3 simulado sobre la población — ¿el despreciado que toca {TARGET:.0%} REVIERTE (gana >{TARGET:.0%})?")
    print("=" * 92)
    line("TODO", R)
    print("  — por FASE del primer roce (¿los dips TEMPRANOS revierten más que los tardíos?):")
    for lo, hi, lab in [(0, 60, "0-60s"), (60, 120, "60-120s"), (120, 195, "120-195s"),
                        (195, 300, "195-300s"), (300, 1e9, ">300")]:
        line(lab, [r for r in R if lo <= r["phase"] < hi])

    print("\n" + "=" * 92)
    print("VEREDICTO (con potencia, sin esperar a los fills en vivo)")
    print("=" * 92)
    wr = sum(r["won"] for r in R) / len(R); se = math.sqrt(wr * (1 - wr) / len(R))
    print(f"  · maker3 en vivo: n=5 (ruido). Aquí: n={len(R)}.")
    if wr - 1.96 * se > TARGET:
        print(f"  → win {wr:.1%} > target {TARGET:.0%} SIGNIFICATIVO → maker3 es +EV en la población. El")
        print("    early-rester SÍ captura el edge; el rojo de n=5 es ruido y convergerá. Llegar temprano funciona.")
    elif wr > TARGET:
        print(f"  → win {wr:.1%} > target {TARGET:.0%} pero no significativo — al borde. Más días de cinta.")
    else:
        print(f"  → win {wr:.1%} <= target {TARGET:.0%}: el despreciado que toca {TARGET:.0%} NO revierte lo")
        print("    suficiente. maker3 es −EV incluso en la población → llegar temprano tampoco basta a este")
        print("    precio. Habría que probar OTRO target (¿más profundo? ¿más alto?) o cerrar. Negativo honesto.")

    print("\nCAVEATS: la cinta se muestrea cada 20s → el 'primer roce' es aproximado (podríamos llenarnos un")
    print("poco antes). El precio de fill se asume = TARGET (nuestro bid). Resolución agregada por Chainlink/spot.")

if __name__ == "__main__":
    main()
